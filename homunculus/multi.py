"""Two minds in one world: speech, and predicting another agent.

P5 exists to test two things the single-agent world could not:

  * H4 fairly. It was falsified in P1 against random-waypoint critters, whose
    intent genuinely does not persist across an occlusion. An LLM agent pursuing
    a drive has persistent goals — walking to food takes many ticks in a
    straight-ish line — so this is the case rollout was supposed to win, and the
    honest retest of a falsified hypothesis.
  * Speech as an ACTION, not a parallel channel. Saying something is a motor act
    with a duration and an audience; it lands in the other agent's perception as
    an event, and only if they are close enough to hear.

Each agent owns a complete stack (own belief, own body, own memory) and sees the
other only through its senses. Neither reads the other's state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import scenario as scenario_mod
from . import sensorium, surprise
from .frame import Frame
from .gate import SurpriseGate
from .motor import Motor, affordances
from .rng import HRng
from .soma import Soma
from .world import Action, Entity
from .worldmodel import WorldModel

HEARING_RANGE = 9.0

# Acts that must never be repeated reflexively. Eating twice is meaningless once
# the food is gone, and re-issuing an utterance without thinking makes the agent
# repeat itself verbatim — which it did, four times running, before this existed.
ONE_SHOT = frozenset({"eat", "say"})


def _habit_if_legal(ag) -> dict | None:
    h = ag._habit
    if h is None or ag.frame is None or ag._habit_uses >= 6:
        return None
    verb = h.get("verb")
    if verb in ONE_SHOT:
        return None
    if verb == "wait":
        return h
    legal = {(a["verb"], a.get("target")) for a in ag.frame.affordances}
    return h if (verb, h.get("target")) in legal else None


@dataclass
class Utterance:
    tick: int
    speaker: str
    text: str

    def to_dict(self) -> dict:
        return {"tick": self.tick, "speaker": self.speaker, "text": self.text}


@dataclass
class Agent:
    """One embodied mind. Owns its belief, body, motor and memory."""

    aid: str
    wm: WorldModel
    soma: Soma
    motor: Motor
    policy: object
    gate: object | None = None
    memory: object | None = None
    drift: surprise.DriftModel = field(default_factory=surprise.DriftModel)
    frame: Frame | None = None
    last_action: Action | None = None
    last_surprise: object | None = None
    decisions: int = 0
    habits: int = 0
    heard: list = field(default_factory=list)
    said: list = field(default_factory=list)
    _habit: dict | None = None
    _habit_uses: int = 0


class MultiRuntime:
    """Advances several agents through one shared world, one tick at a time.

    Agents act in a fixed sorted order each tick so the run stays reproducible;
    within a tick each acts on the world as it stands, so the second agent sees
    the first agent's move. That asymmetry is real and intentional — it is what
    makes the other agent something to be predicted rather than a fixture.
    """

    def __init__(self, seed: int, scenario: str = "apartment", policies=None,
                 gates=True, memories=None):
        self.rng = HRng(seed)
        self.world = scenario_mod.build(scenario)
        self.tick = 0
        self.utterances: list[Utterance] = []

        # The second agent shares the FIRST agent's half of the apartment.
        # Placed across the partition it never met the first one: each half is
        # self-sufficient in food, warmth and landmarks, so neither had any
        # reason to cross and P5 had nothing to observe.
        self.world.entities["agent2"] = Entity("agent2", "agent", 5, 17, heading=270.0)

        lm = scenario_mod.landmarks(self.world)
        self.agents: dict[str, Agent] = {}
        for aid in ("agent", "agent2"):
            e = self.world.entities[aid]
            wm = WorldModel((e.x, e.y, e.heading), lm, walls=self.world.walls,
                            bounds=(self.world.w, self.world.h))
            pol = (policies or {}).get(aid)
            if pol is None:
                from .policy import ReactivePolicy
                pol = ReactivePolicy()
            self.agents[aid] = Agent(
                aid=aid, wm=wm, soma=Soma(), motor=Motor(wm), policy=pol,
                gate=SurpriseGate() if gates else None,
                memory=(memories or {}).get(aid),
            )

        for aid, ag in sorted(self.agents.items()):
            obs = sensorium.observe(self.world, origin_id=aid)
            ag.wm.ingest(obs, 0)
            ag.frame = self._frame(ag, {o.id for o in obs})

    # --- frames -----------------------------------------------------------
    def _frame(self, ag: Agent, observed: set[str]) -> Frame:
        views = ag.wm.entity_views(self.tick, observed)
        f = Frame(
            tick=self.tick,
            pose=ag.wm.pose,
            pose_conf=ag.wm.pose_conf,
            efference=ag.last_action.to_dict() if ag.last_action else None,
            drives=ag.soma.to_dict(),
            valence=ag.soma.valence,
            arousal=ag.soma.arousal,
            entities=views,
            affordances=affordances(ag.wm, views, self.tick, unavailable=ag.motor._failed),
            events=[{"kind": "heard", **u.to_dict()} for u in ag.heard[-3:]],
        )
        # Speech is an affordance only when there is someone plausibly in
        # earshot — you cannot address an empty room.
        if any(v.kind == "agent" and v.range <= HEARING_RANGE for v in views):
            f.affordances = sorted(
                f.affordances + [{"verb": "say"}],
                key=lambda a: (a["verb"], a.get("target", "")),
            )
        c = ag.motor.current
        f.budget = {"busy": ag.motor.busy(),
                    "commitment": c.to_dict() if c else None,
                    "decisions": ag.decisions}
        return f

    # --- speech -----------------------------------------------------------
    def _speak(self, ag: Agent, text: str) -> None:
        u = Utterance(self.tick, ag.aid, text[:160])
        self.utterances.append(u)
        ag.said.append(u)
        speaker = self.world.entities[ag.aid]
        for oid, other in sorted(self.agents.items()):
            if oid == ag.aid:
                continue
            o = self.world.entities[oid]
            d = ((speaker.x - o.x) ** 2 + (speaker.y - o.y) ** 2) ** 0.5
            if d <= HEARING_RANGE:
                other.heard.append(u)

    # --- one tick ---------------------------------------------------------
    def step(self) -> dict:
        self.tick += 1
        t = self.tick
        ev: dict = {"type": "tick", "t": t, "agents": {}}
        first = sorted(self.agents)[0]

        for aid in sorted(self.agents):
            ag = self.agents[aid]
            ag.motor.tick = t
            sub: dict = {}

            habit = _habit_if_legal(ag)
            if ag.gate is None:
                open_now = None if ag.motor.busy() else "idle"
            else:
                open_now = ag.gate.should_open(t, ag.motor.busy(), habit is not None)

            if open_now is None and not ag.motor.busy() and habit is not None:
                ag.habits += 1
                ag._habit_uses += 1
                choice = habit
                sub["gate"] = "habit"
            elif open_now:
                if ag.gate is not None:
                    ag.gate.opened(t, open_now)
                choice = ag.policy.choose(ag.frame, ag.wm, ag.soma,
                                          self.rng.stream(f"policy:{aid}"))
                ag.decisions += 1
                ag._habit, ag._habit_uses = dict(choice), 0
                sub["gate"] = open_now
            else:
                choice = None

            if choice is not None:
                sub["decision"] = choice
                if choice["verb"] == "say":
                    self._speak(ag, choice.get("text") or "...")
                    sub["said"] = choice.get("text")
                    c = ag.motor.start("wait")
                    c.meta["duration"] = 2
                else:
                    c = ag.motor.start(choice["verb"], choice.get("target"))
                    if choice.get("duration"):
                        c.meta["duration"] = choice["duration"]

            action = ag.motor.step()
            # Scripted movers advance exactly once per tick, on the first
            # agent's turn — otherwise critters would move twice as fast in a
            # two-agent world and the physics would depend on the population.
            res = self.world.apply(action, self.rng.stream("world"), t,
                                   actor=aid, move_others=(aid == first))
            ag.motor.note_result(bool(res.get("blocked")), res.get("consumed"))

            obs = sensorium.observe(self.world, origin_id=aid)
            percepts = sensorium.observed_events(self.world, res)
            ag.wm.predict_pose(action)
            for p in percepts:
                if p.get("kind") == "bump":
                    ag.wm.apply_bump(action)
                    if p.get("dir"):
                        ag.wm.correct_from_bump(p["dir"])
            ag.wm.note_traffic(ag.wm._area(ag.wm.pose[:2]), 0.5)

            rep = surprise.compute(ag.wm, obs, t, ag.drift)
            if ag.gate is not None:
                ag.gate.observe(rep.scalar)
            for tag in rep.existence:
                if tag.startswith("-"):
                    ag.wm.disconfirm(tag[1:])
            ag.wm.ingest(obs, t, events=None, action=action)

            body = self.world.entities[aid]
            warm = self.world.entity_at(body.x, body.y, kinds={"warmth"}) is not None
            ag.soma.step(near_food_eaten=bool(res.get("consumed")),
                         near_warmth=warm,
                         moved=bool(res.get("moves")) and action.verb == "move")
            ag.soma.latch()

            ag.last_action = action
            ag.last_surprise = rep
            ag.frame = self._frame(ag, {o.id for o in obs})
            if ag.memory is not None:
                ag.memory.observe(t, ag.frame, rep, res)

            sub["action"] = action.to_dict()
            if res.get("consumed"):
                sub["consumed"] = res["consumed"]
            if rep.scalar > 0:
                sub["surprise"] = round(rep.scalar, 3)
            ev["agents"][aid] = sub

        return ev

    def pose_error(self, aid: str) -> float:
        e = self.world.entities[aid]
        p = self.agents[aid].wm.pose
        return ((p[0] - e.x) ** 2 + (p[1] - e.y) ** 2) ** 0.5
