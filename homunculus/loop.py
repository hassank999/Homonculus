"""The tick scheduler and scenario builder.

For P0 the loop is unconditional — the agent acts every tick. The surprise gate
that makes cognition affordable (PLAN.md H1) lands in P3; here we just need a
reproducible run and a clean event stream. `run` returns the in-memory event
list plus any requested state checkpoints, so the determinism test can compare
live state against replayed state.
"""

from __future__ import annotations

from .agent import RandomAgent
from .rng import HRng
from .world import Entity, World


def build_scenario(name: str) -> World:
    """Deterministic starting layouts. Initial positions are fixed (not seeded)
    so the scenario itself is stable; only movement is stochastic."""
    if name != "room":
        raise ValueError(f"unknown scenario: {name!r}")

    w = h = 20
    walls = set()
    for x in range(w):
        walls.add((x, 0))
        walls.add((x, h - 1))
    for y in range(h):
        walls.add((0, y))
        walls.add((w - 1, y))
    # a short interior wall segment
    for y in range(5, 10):
        walls.add((10, y))

    entities = [
        Entity("agent", "agent", 10, 10),
        Entity("c00", "critter", 3, 3),
        Entity("c01", "critter", 16, 3),
        Entity("c02", "critter", 3, 16),
        Entity("c03", "critter", 16, 16),
        Entity("c04", "critter", 7, 10),
        Entity("c05", "critter", 13, 10),
    ]
    return World(w, h, walls, entities)


def _run_start_event(seed: int, scenario: str, world: World, ticks: int) -> dict:
    return {
        "type": "run_start",
        "seed": int(seed),
        "scenario": scenario,
        "config": {"w": world.w, "h": world.h, "ticks": ticks},
        "walls": sorted(list(p) for p in world.walls),
        "entities": [
            {"id": e.id, "kind": e.kind, "x": e.x, "y": e.y}
            for e in sorted(world.entities.values(), key=lambda e: e.id)
        ],
    }


def run(seed: int, ticks: int, scenario: str = "room", checkpoints=None):
    """Execute a run. Returns (events, checkpoints) where checkpoints maps a
    requested tick number -> world.snapshot() captured live at that tick."""
    want = set(checkpoints or ())
    rng = HRng(seed)
    world = build_scenario(scenario)
    agent = RandomAgent()

    events: list[dict] = [_run_start_event(seed, scenario, world, ticks)]
    snaps: dict[int, dict] = {}
    if 0 in want:
        snaps[0] = world.snapshot()

    for t in range(1, ticks + 1):
        action = agent.act(world, rng.stream("agent"))
        events.append(world.apply(action, rng.stream("world"), t))
        if t in want:
            snaps[t] = world.snapshot()

    events.append({"type": "run_end", "t": ticks, "ticks": ticks})
    return events, snaps
