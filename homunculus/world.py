"""The tick-discrete gridworld.

Deliberately dumb — a test fixture, not a product (PLAN.md §2). Two details
exist specifically to make the thesis testable:

  * Critters are GOAL-DIRECTED, not random walkers. They pick a waypoint and
    walk toward it. Without real intent to infer, rollout could never beat
    diffusion and H4 would be untestable by construction.
  * Blocked moves are reported. The agent updates its believed pose from its own
    motor command (efference copy), so a move blocked by an unseen wall makes
    belief and reality diverge — pose drift with no artificial noise needed.

`apply` returns a concrete tick event (outcomes, not dice) so replay stays a
pure RNG-free fold.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DIRS: dict[str, tuple[int, int]] = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}
HEADING: dict[str, float] = {"E": 0.0, "S": 90.0, "W": 180.0, "N": 270.0}

# Two animate archetypes, deliberately different in how predictable they are:
#   critter  — re-randomizes its waypoint on arrival; intent does NOT persist
#   resident — commutes between fixed anchors with long dwells; intent DOES
# The contrast is the point: it locates the boundary where intent-rollout beats
# a no-motion baseline, instead of assuming it always does.
MOVABLE = frozenset({"critter", "resident"})

# Probability an agent move silently fails without producing a bump percept.
SLIP_P = 0.02


@dataclass
class Entity:
    id: str
    kind: str
    x: int
    y: int
    heading: float = 0.0
    state: dict = field(default_factory=dict)
    goal: tuple[int, int] | None = None


@dataclass(frozen=True)
class Action:
    verb: str
    dir: str | None = None
    target: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"verb": self.verb}
        if self.dir is not None:
            d["dir"] = self.dir
        if self.target is not None:
            d["target"] = self.target
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

    def entity_at(self, x: int, y: int, kinds=None) -> Entity | None:
        for eid in sorted(self.entities):
            e = self.entities[eid]
            if e.x == x and e.y == y and (kinds is None or e.kind in kinds):
                return e
        return None

    # --- critter intent ---------------------------------------------------
    def _pick_goal(self, e: Entity, rng) -> None:
        for _ in range(32):
            gx = rng.randrange(1, self.w - 1)
            gy = rng.randrange(1, self.h - 1)
            if self.passable(gx, gy):
                e.goal = (gx, gy)
                return
        e.goal = (e.x, e.y)

    def _step_toward_goal(self, e: Entity, rng) -> None:
        """Greedy step toward the waypoint, with a small stochastic detour so the
        motion is predictable-in-tendency but not perfectly deterministic."""
        if e.goal is None or (e.x, e.y) == e.goal:
            self._pick_goal(e, rng)
        gx, gy = e.goal
        dx, dy = gx - e.x, gy - e.y
        prefs: list[str] = []
        if abs(dx) >= abs(dy):
            if dx: prefs.append("E" if dx > 0 else "W")
            if dy: prefs.append("S" if dy > 0 else "N")
        else:
            if dy: prefs.append("S" if dy > 0 else "N")
            if dx: prefs.append("E" if dx > 0 else "W")
        if rng.random() < 0.15:
            prefs = ["N", "S", "E", "W"]
            rng.shuffle(prefs)
        for d in prefs:
            if self._try_move(e, d):
                e.heading = HEADING[d]
                return

    def _step_routine(self, e: Entity, rng) -> None:
        """A resident commutes between two fixed anchors and dwells at each.

        Unlike a critter, its heading persists for many ticks, so a heading
        observed before an occlusion still predicts where it will be after."""
        anchors = e.state.get("anchors")
        if not anchors:
            return
        idx = e.state.get("target", 0)
        tgt = tuple(anchors[idx])
        if (e.x, e.y) == tgt:
            dwell = e.state.get("dwell", 0)
            if dwell > 0:
                e.state["dwell"] = dwell - 1
            else:
                e.state["target"] = (idx + 1) % len(anchors)
                e.state["dwell"] = rng.randrange(20, 60)
            return
        e.goal = tgt
        dx, dy = tgt[0] - e.x, tgt[1] - e.y
        prefs: list[str] = []
        if abs(dx) >= abs(dy):
            if dx: prefs.append("E" if dx > 0 else "W")
            if dy: prefs.append("S" if dy > 0 else "N")
        else:
            if dy: prefs.append("S" if dy > 0 else "N")
            if dx: prefs.append("E" if dx > 0 else "W")
        for d in ("N", "S", "E", "W"):
            if d not in prefs:
                prefs.append(d)
        for d in prefs:
            if self._try_move(e, d):
                e.heading = HEADING[d]
                return

    # --- tick -------------------------------------------------------------
    def apply(self, action: Action, world_rng, t: int) -> dict:
        """Advance one tick. Fixed consumption order — agent, then movers in
        sorted id order — is what keeps the run reproducible."""
        moves: list[dict] = []
        blocked = False
        slipped_flag = False
        consumed = None

        agent = self.entities["agent"]
        before = (agent.x, agent.y)
        if action.verb == "move" and action.dir in DIRS:
            agent.heading = HEADING[action.dir]
            # A slip is a move that silently fails with NO bump percept. This is
            # the only undetectable motor error, and therefore the only true
            # source of dead-reckoning drift — which is precisely what landmark
            # re-localization exists to correct. Rolled every move so RNG
            # consumption stays deterministic.
            slipped = world_rng.random() < SLIP_P
            if slipped:
                slipped_flag = True
            if not slipped and not self._try_move(agent, action.dir):
                blocked = True
        elif action.verb == "eat":
            f = self.entity_at(agent.x, agent.y, kinds={"food"})
            if f is not None and f.state.get("available", True):
                f.state["available"] = False
                f.state["respawn_at"] = t + 200
                consumed = f.id
        if (agent.x, agent.y) != before:
            moves.append({"id": "agent", "from": list(before), "to": [agent.x, agent.y]})

        for eid in sorted(self.entities):
            e = self.entities[eid]
            if e.kind not in MOVABLE:
                continue
            b = (e.x, e.y)
            if e.kind == "resident":
                self._step_routine(e, world_rng)
            else:
                self._step_toward_goal(e, world_rng)
            if (e.x, e.y) != b:
                moves.append({"id": eid, "from": list(b), "to": [e.x, e.y]})

        respawned: list[str] = []
        for eid in sorted(self.entities):
            e = self.entities[eid]
            if e.kind == "food" and not e.state.get("available", True):
                if t >= e.state.get("respawn_at", 0):
                    e.state["available"] = True
                    e.state.pop("respawn_at", None)
                    respawned.append(eid)

        moves.sort(key=lambda m: m["id"])
        ev: dict = {"type": "tick", "t": t, "action": action.to_dict(), "moves": moves}
        if blocked:
            ev["blocked"] = True
        if slipped_flag:
            # Logged for instrumentation/replay only — never surfaced to the
            # agent, since the whole point is that a slip is not felt.
            ev["slipped"] = True
        if consumed:
            ev["consumed"] = consumed
        if respawned:
            ev["respawned"] = respawned
        return ev

    def snapshot(self) -> dict[str, tuple[int, int]]:
        return {eid: (e.x, e.y) for eid, e in self.entities.items()}
