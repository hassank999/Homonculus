"""The trivial tick-discrete gridworld.

Deliberately dumb — this is a test fixture, not a product (PLAN.md §2). It holds
entities on an integer grid with static walls, applies the agent's action, then
advances scripted "critter" movers by a seeded random walk. `apply` returns a
concrete tick event (the outcomes, not the dice) so that replay can reconstruct
state by pure folding, with no RNG in the read path.
"""

from __future__ import annotations

from dataclasses import dataclass

# Screen convention: +y is down. Directions the model/critters may move.
DIRS: dict[str, tuple[int, int]] = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}

# Kinds that take an autonomous turn each tick.
MOVABLE = frozenset({"critter"})


@dataclass
class Entity:
    id: str
    kind: str
    x: int
    y: int


@dataclass(frozen=True)
class Action:
    verb: str                 # "move" | "wait"
    dir: str | None = None    # one of DIRS when verb == "move"

    def to_dict(self) -> dict:
        d: dict = {"verb": self.verb}
        if self.dir is not None:
            d["dir"] = self.dir
        return d


class World:
    def __init__(self, w: int, h: int, walls, entities):
        self.w = w
        self.h = h
        self.walls: set[tuple[int, int]] = {tuple(p) for p in walls}
        self.entities: dict[str, Entity] = {e.id: e for e in entities}

    # --- geometry ---------------------------------------------------------
    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def passable(self, x: int, y: int) -> bool:
        return self.in_bounds(x, y) and (x, y) not in self.walls

    def _try_move(self, e: Entity, direction: str) -> bool:
        dx, dy = DIRS[direction]
        nx, ny = e.x + dx, e.y + dy
        if self.passable(nx, ny):
            e.x, e.y = nx, ny
            return True
        return False

    # --- tick -------------------------------------------------------------
    def apply(self, action: Action, world_rng, t: int) -> dict:
        """Advance one tick. Fixed consumption order — agent first, then movers
        in sorted id order — is what keeps the run reproducible."""
        moves: list[dict] = []

        agent = self.entities["agent"]
        before = (agent.x, agent.y)
        if action.verb == "move" and action.dir in DIRS:
            self._try_move(agent, action.dir)
        if (agent.x, agent.y) != before:
            moves.append({"id": "agent", "from": list(before), "to": [agent.x, agent.y]})

        for eid in sorted(self.entities):
            e = self.entities[eid]
            if e.kind not in MOVABLE:
                continue
            choice = world_rng.choice(("N", "S", "E", "W", "stay"))
            b = (e.x, e.y)
            if choice != "stay":
                self._try_move(e, choice)
            if (e.x, e.y) != b:
                moves.append({"id": eid, "from": list(b), "to": [e.x, e.y]})

        moves.sort(key=lambda m: m["id"])
        return {"type": "tick", "t": t, "action": action.to_dict(), "moves": moves}

    def snapshot(self) -> dict[str, tuple[int, int]]:
        """Full positional state, in the same shape Replay.state_at returns."""
        return {eid: (e.x, e.y) for eid, e in self.entities.items()}
