"""The tick scheduler.

Each tick runs the full cycle:

    act -> world.apply -> sense -> predict-pose -> compare (surprise) -> ingest

Order matters and is the whole architecture in six steps. Surprise is computed
BEFORE ingest, because once observations are folded into the beliefs there is
nothing left to be surprised about — the comparison has to happen against the
belief as it stood a moment ago.

For P1 the loop is unconditional (the agent acts every tick). The surprise gate
that makes cognition affordable, and the concurrency governor Together's limiter
requires, arrive in P3.
"""

from __future__ import annotations

from . import scenario as scenario_mod
from . import sensorium, surprise
from .agent import RandomAgent
from .rng import HRng
from .worldmodel import WorldModel


def _run_start_event(seed: int, name: str, world, ticks: int) -> dict:
    return {
        "type": "run_start",
        "seed": int(seed),
        "scenario": name,
        "config": {"w": world.w, "h": world.h, "ticks": ticks},
        "walls": sorted(list(p) for p in world.walls),
        "entities": [
            {"id": e.id, "kind": e.kind, "x": e.x, "y": e.y}
            for e in sorted(world.entities.values(), key=lambda e: e.id)
        ],
    }


class Runtime:
    """Holds the whole stack for one run and advances it one tick at a time."""

    def __init__(self, seed: int, scenario: str = "apartment", mind=None):
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
        self.mind = mind or RandomAgent()
        self.tick = 0
        self.last_action = None
        self.last_surprise = None
        self.last_frame = None

        # Bootstrap: perceive once so the agent starts with beliefs, not a void.
        obs = sensorium.observe(self.world)
        self.wm.ingest(obs, 0)

    def step(self) -> dict:
        self.tick += 1
        t = self.tick

        action = self.mind.act(self.world, self.rng.stream("agent"))
        ev = self.world.apply(action, self.rng.stream("world"), t)

        obs = sensorium.observe(self.world)
        events = sensorium.observed_events(self.world, ev)

        # Believed pose advances from the motor command alone. A bump is felt,
        # so it is corrected immediately (before interpreting what is seen); a
        # slip is not felt, so it silently becomes drift.
        self.wm.predict_pose(action)
        for ev_p in events:
            if ev_p.get("kind") == "bump":
                self.wm.apply_bump(action)
        self.wm.note_traffic(self.wm._area(self.wm.pose[:2]), 0.5)

        # Surprise is computed BEFORE ingest — once observations are folded into
        # the beliefs there is nothing left to be surprised about. Note that pose
        # error shows up here as apparent entity error, which is exactly right:
        # "everything is in the wrong place" is what being lost feels like, and
        # it is the cue to re-localize.
        rep = surprise.compute(self.wm, obs, t, self.drift)
        self.wm.ingest(obs, t, events=None, action=action)

        self.last_action = action
        self.last_surprise = rep
        if rep.scalar > 0.0 or events:
            ev["surprise"] = rep.to_dict()
        if events:
            ev["percepts"] = events
        ev["pose"] = [round(v, 3) for v in self.wm.pose]
        ev["pose_conf"] = round(self.wm.pose_conf, 3)
        return ev

    def pose_error(self) -> float:
        """Ground-truth check — for instrumentation only, never fed to the agent."""
        a = self.world.entities["agent"]
        return ((self.wm.pose[0] - a.x) ** 2 + (self.wm.pose[1] - a.y) ** 2) ** 0.5


def run(seed: int, ticks: int, scenario: str = "apartment", checkpoints=None,
        mind=None):
    """Execute a run. Returns (events, checkpoints)."""
    want = set(checkpoints or ())
    rt = Runtime(seed, scenario, mind=mind)
    events = [_run_start_event(seed, scenario, rt.world, ticks)]
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
    """Back-compat shim for P0 callers."""
    return scenario_mod.build(name if name != "room" else "apartment")
