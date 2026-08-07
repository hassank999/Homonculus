"""The tick scheduler.

Each tick runs the full cycle:

    decide (only if idle) -> motor.step -> world.apply -> sense
      -> pose update -> surprise -> ingest -> soma

Order matters and is the architecture in one page. Two points are load-bearing:

  * Surprise is computed BEFORE ingest. Once observations are folded into the
    beliefs there is nothing left to be surprised about.
  * The decision-maker is consulted ONLY when the motor is idle or the current
    commitment ends. A `goto` across a room is forty ticks of zero deliberation.
    In P3 this is what makes surprise-gated cognition affordable.
"""

from __future__ import annotations

from . import scenario as scenario_mod
from . import sensorium, surprise
from .frame import Frame
from .motor import Motor, affordances
from .policy import ReactivePolicy
from .rng import HRng
from .soma import Soma
from .worldmodel import WorldModel


def _run_start_event(seed: int, name: str, world, ticks: int, policy: str) -> dict:
    return {
        "type": "run_start",
        "seed": int(seed),
        "scenario": name,
        "policy": policy,
        "config": {"w": world.w, "h": world.h, "ticks": ticks},
        "walls": sorted(list(p) for p in world.walls),
        "entities": [
            {"id": e.id, "kind": e.kind, "x": e.x, "y": e.y}
            for e in sorted(world.entities.values(), key=lambda e: e.id)
        ],
    }


class Runtime:
    """Holds the whole stack for one run and advances it one tick at a time."""

    def __init__(self, seed: int, scenario: str = "apartment", policy=None,
                 gate=None, governor=None, memory=None, sleep_every: int = 0):
        self.rng = HRng(seed)
        self.scenario = scenario
        self.world = scenario_mod.build(scenario)
        a = self.world.entities["agent"]
        self.wm = WorldModel(
            (a.x, a.y, a.heading),
            scenario_mod.landmarks(self.world),
            walls=self.world.walls,
            bounds=(self.world.w, self.world.h),
        )
        self.drift = surprise.DriftModel()
        self.soma = Soma()
        self.motor = Motor(self.wm)
        self.policy = policy or ReactivePolicy()
        # No gate == consult the decision-maker whenever the motor is idle
        # (the P2 behaviour). A gate additionally allows surprise and heartbeat
        # to interrupt an action in progress.
        self.gate = gate
        self.governor = governor
        self.memory = memory
        self.sleep_every = sleep_every
        self.tick = 0
        self.last_action = None
        self.last_surprise = None
        self.frame: Frame | None = None
        self.decisions = 0            # times the decision-maker was consulted
        self._last_choice = None
        self._repeat = 0
        self._habit: dict | None = None      # the standing plan, re-issued reflexively
        self.habits = 0                      # times acted without consulting
        self._last_reported = None           # commitment whose ending was logged

        # A habit is only reusable while it remains legal and useful; these
        # guards stop the agent repeating a plan the world has moved past.
        self._habit_uses = 0

        obs = sensorium.observe(self.world)
        self.wm.ingest(obs, 0)
        self.frame = self._build_frame({o.id for o in obs})

    # --- frame ------------------------------------------------------------
    def _build_frame(self, observed_ids: set[str]) -> Frame:
        views = self.wm.entity_views(self.tick, observed_ids)
        f = Frame(
            tick=self.tick,
            pose=self.wm.pose,
            pose_conf=self.wm.pose_conf,
            efference=self.last_action.to_dict() if self.last_action else None,
            drives=self.soma.to_dict(),
            valence=self.soma.valence,
            arousal=self.soma.arousal,
            entities=views,
            affordances=affordances(self.wm, views, self.tick,
                                    unavailable=self.motor._failed),
        )
        c = self.motor.current
        f.budget = {
            "busy": self.motor.busy(),
            "commitment": c.to_dict() if c else None,
            "decisions": self.decisions,
        }
        return f

    def _habit_if_legal(self) -> dict | None:
        """The standing plan, if repeating it still makes sense.

        A habit that is no longer available (the food is gone, the target is
        forgotten) must not be executed blindly — reflexive action is cheap, not
        free of judgement. Repeats are also capped so a habit cannot become an
        infinite loop that never re-consults the mind.
        """
        h = self._habit
        if h is None or self.frame is None:
            return None
        if self._habit_uses >= 6:
            return None
        verb = h.get("verb")
        if verb == "wait":
            return h
        legal = {
            (a["verb"], a.get("target"))
            for a in self.frame.affordances
        }
        if (verb, h.get("target")) not in legal:
            return None
        # One-shot acts must not be repeated reflexively: eating again once the
        # food is gone is meaningless, and re-issuing an utterance without
        # thinking makes the agent repeat itself verbatim.
        if verb in ("eat", "say"):
            return None
        return h

    def _why(self, reason, frame, prev_surprise) -> dict:
        """The state that produced a decision: what was pressing, what was
        surprising, and what was actually in view at the moment of choosing."""
        drive, deficit = self.soma.worst()
        why: dict = {
            "drive": drive,
            "value": round(self.soma.drives[drive].value, 3),
            "valence": round(self.soma.valence, 3),
            "arousal": round(self.soma.arousal, 3),
        }
        if reason == "surprise" and prev_surprise is not None:
            top = prev_surprise.top(2)
            if top:
                why["surprised_by"] = [[k, round(v, 2)] for k, v in top]
            if prev_surprise.existence:
                why["existence"] = sorted(prev_surprise.existence)[:3]
        if frame is not None:
            # Only what could plausibly bear on the choice: near things it can
            # see, plus anything it is notably unsure about.
            seen = [
                {"id": v.id, "kind": v.kind, "range": v.range,
                 "conf": round(v.conf, 2), "age": v.age, "obs": v.observed}
                for v in frame.entities
                if (v.observed and v.range <= 8.0) or v.conf < 0.6
            ]
            seen.sort(key=lambda s: (not s["obs"], s["range"]))
            why["saw"] = seen[:5]
        return why

    # --- one tick ---------------------------------------------------------
    def step(self) -> dict:
        self.tick += 1
        t = self.tick
        self.motor.tick = t
        pre_frame = self.frame
        pre_surprise = self.last_surprise

        # 1. Deliberate only when there is nothing already underway.
        decided = None
        reason = None
        habit = self._habit_if_legal()
        if self.gate is None:
            open_now = None if self.motor.busy() else "idle"
        else:
            open_now = self.gate.should_open(t, self.motor.busy(), habit is not None)

        if open_now is None and not self.motor.busy() and habit is not None:
            # Reflexive re-issue: the body finished something, nothing surprising
            # happened, so repeat the standing plan without thinking.
            reason = "habit"
            decided = habit
            self.habits += 1
            self._habit_uses += 1
            c = self.motor.start(habit["verb"], habit.get("target"))
            if habit.get("duration"):
                c.meta["duration"] = habit["duration"]
            open_now = None

        elif open_now:
            reason = open_now
            if self.gate is not None:
                self.gate.opened(t, open_now)
                if open_now != "idle" and self.motor.busy():
                    # Only reached when interrupts are explicitly enabled.
                    self.motor.interrupt(open_now)
            if self.governor is not None:
                self.governor.acquire()
            choice = self.policy.choose(
                self.frame, self.wm, self.soma, self.rng.stream("policy")
            )
            self.decisions += 1
            decided = choice
            # Thrash guard: a commitment that terminates without doing anything,
            # repeated, means the agent is chasing a belief it cannot resolve.
            key = (choice["verb"], choice.get("target"))
            if key == self._last_choice:
                self._repeat += 1
            else:
                self._repeat, self._last_choice = 0, key
            if self._repeat >= 3:
                choice = {"verb": "wait", "duration": 15}
                decided = choice
                self._repeat = 0
            c = self.motor.start(choice["verb"], choice.get("target"))
            if choice.get("duration"):
                c.meta["duration"] = choice["duration"]
            self._habit = dict(choice)
            self._habit_uses = 0

        # 2. The commitment produces this tick's motor command.
        action = self.motor.step()
        ev = self.world.apply(action, self.rng.stream("world"), t)
        self.motor.note_result(bool(ev.get("blocked")), ev.get("consumed"))

        # 3. Perceive.
        obs = sensorium.observe(self.world)
        events = sensorium.observed_events(self.world, ev)

        # 4. Believed pose from the motor command alone; bumps are felt,
        #    slips are not (and therefore become drift).
        self.wm.predict_pose(action)
        for p in events:
            if p.get("kind") == "bump":
                self.wm.apply_bump(action)
                # The collision also localizes: only some positions are
                # consistent with hitting a wall in that direction.
                if p.get("dir"):
                    self.wm.correct_from_bump(p["dir"])
        self.wm.note_traffic(self.wm._area(self.wm.pose[:2]), 0.5)

        # 5. Compare before folding in.
        rep = surprise.compute(self.wm, obs, t, self.drift)
        if self.gate is not None:
            # Surprise accumulates between decisions: many small anomalies
            # should eventually earn a thought even if none alone would.
            self.gate.observe(rep.scalar)
        # Act on disconfirmation: anything confidently expected in view and not
        # seen loses confidence. Absence of evidence is evidence.
        for tag in rep.existence:
            if tag.startswith("-"):
                self.wm.disconfirm(tag[1:])
        self.wm.ingest(obs, t, events=None, action=action)

        # 6. Body.
        agent = self.world.entities["agent"]
        warmth = self.world.entity_at(agent.x, agent.y, kinds={"warmth"}) is not None
        self.soma.step(
            near_food_eaten=bool(ev.get("consumed")),
            near_warmth=warmth,
            moved=bool(ev.get("moves")) and action.verb == "move",
        )
        self.soma.latch()

        self.last_action = action
        self.last_surprise = rep
        self.frame = self._build_frame({o.id for o in obs})

        # 7. Remember. Write-through is selective: a tick that went exactly as
        #    predicted taught the agent nothing worth storing.
        if self.memory is not None:
            self.memory.observe(t, self.frame, rep, ev)
            if self.sleep_every and t % self.sleep_every == 0:
                ev["sleep"] = self.memory.sleep(t)

        # Event record.
        if decided:
            ev["decision"] = decided
            if reason:
                ev["gate"] = reason
            # The causal chain behind this choice. Logged only at decision
            # points, so it costs little, and it is what turns an event log
            # into an account of WHY rather than merely what.
            ev["because"] = self._why(reason, pre_frame, pre_surprise)
            note = getattr(self.policy, "last_note", "") or ""
            if note and reason != "habit":
                ev["note"] = note[:240]
            pred = getattr(self.policy, "last_prediction", None)
            if pred and reason != "habit":
                ev["expect"] = pred.get("expect_surprise")
        if rep.scalar > 0.0:
            ev["surprise"] = rep.to_dict()
        if events:
            ev["percepts"] = events
        c = self.motor.current
        if c and c.status != "running" and c is not self._last_reported:
            # Report each commitment's ending exactly once, so the stream can
            # close the loop on an intention rather than repeating it.
            ev["commitment"] = c.to_dict()
            self._last_reported = c
            if self.memory is not None:
                # Procedural learning: what actually worked, in what situation.
                drive, _ = self.soma.worst()
                self.memory.procedural.record(
                    drive, c.verb, c.target, c.status == "done"
                )
        ev["pose"] = [round(v, 3) for v in self.wm.pose]
        ev["pose_conf"] = round(self.wm.pose_conf, 3)
        ev["drives"] = self.soma.to_dict()
        ev["valence"] = round(self.soma.valence, 3)
        return ev

    def pose_error(self) -> float:
        """Ground-truth check — instrumentation only, never fed to the agent."""
        a = self.world.entities["agent"]
        return ((self.wm.pose[0] - a.x) ** 2 + (self.wm.pose[1] - a.y) ** 2) ** 0.5


def run(seed: int, ticks: int, scenario: str = "apartment", checkpoints=None,
        policy=None):
    """Execute a run. Returns (events, checkpoints)."""
    want = set(checkpoints or ())
    rt = Runtime(seed, scenario, policy=policy)
    events = [_run_start_event(seed, scenario, rt.world, ticks, rt.policy.name)]
    snaps: dict[int, dict] = {}
    if 0 in want:
        snaps[0] = rt.world.snapshot()

    for t in range(1, ticks + 1):
        events.append(rt.step())
        if t in want:
            snaps[t] = rt.world.snapshot()

    events.append({"type": "run_end", "t": ticks, "ticks": ticks})
    return events, snaps


def build_scenario(name: str):
    """Back-compat shim for early callers."""
    return scenario_mod.build(name if name != "room" else "apartment")
