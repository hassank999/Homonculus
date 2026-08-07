"""Durative action: commitments that run across many ticks.

Walking across a room is not one tick. Without this module the mind would have
to re-emit "walk" forty times, and the surprise gate in P3 could never pay off
— so this is what actually makes gated cognition affordable.

A commitment is started once, then `step`ped by the loop each tick. It closes
its control loop on the agent's BELIEVED map (worldmodel), never ground truth,
so a stale belief produces a plan that fails honestly — which is itself
information worth waking the mind for.

Affordances are generated from perception rather than prompted, which prunes an
otherwise unbounded (verb, noun) action space down to what is actually possible
right now.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from .world import DIRS, Action

# Commitment lifecycle.
RUNNING, DONE, FAILED = "running", "done", "failed"


def astar(start, goal, passable, bounds, limit: int = 4000):
    """Shortest grid path over cells the agent BELIEVES are passable."""
    (w, h) = bounds
    if start == goal:
        return [start]
    openq = [(0, 0, start, None)]
    came: dict = {}
    gscore = {start: 0}
    seen = 0
    while openq and seen < limit:
        _f, g, cur, parent = heapq.heappop(openq)
        if cur in came:
            continue
        came[cur] = parent
        seen += 1
        if cur == goal:
            path = [cur]
            while came[path[-1]] is not None:
                path.append(came[path[-1]])
            return list(reversed(path))
        cx, cy = cur
        for dx, dy in ((0, -1), (0, 1), (1, 0), (-1, 0)):
            nxt = (cx + dx, cy + dy)
            if not (0 <= nxt[0] < w and 0 <= nxt[1] < h) or not passable(nxt):
                continue
            ng = g + 1
            if ng < gscore.get(nxt, 1 << 30):
                gscore[nxt] = ng
                hcost = abs(nxt[0] - goal[0]) + abs(nxt[1] - goal[1])
                heapq.heappush(openq, (ng + hcost, ng, nxt, cur))
    return None


@dataclass
class Commitment:
    verb: str
    target: str | None = None
    status: str = RUNNING
    reason: str = ""
    ticks: int = 0
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"verb": self.verb, "status": self.status, "ticks": self.ticks}
        if self.target:
            d["target"] = self.target
        if self.reason:
            d["reason"] = self.reason
        return d


class Motor:
    """Owns the current commitment and turns it into one Action per tick."""

    MAX_TICKS = 300

    def __init__(self, wm):
        self.wm = wm
        self.current: Commitment | None = None
        self._path: list[tuple[int, int]] = []
        self._stuck = 0
        self._last_here: tuple[int, int] | None = None
        self._last_dir = "N"
        self._last_target_cell: tuple[int, int] | None = None
        self._blocked: dict[tuple[int, int], int] = {}
        self.tick = 0
        self.outcomes: dict[str, int] = {}

    # --- lifecycle --------------------------------------------------------
    def busy(self) -> bool:
        return self.current is not None and self.current.status == RUNNING

    def start(self, verb: str, target: str | None = None) -> Commitment:
        c = Commitment(verb=verb, target=target)
        self.current = c
        self._path = []
        self._stuck = 0
        if verb == "goto" and target:
            goal = self._target_cell(target)
            here = (int(round(self.wm.pose[0])), int(round(self.wm.pose[1])))
            if goal is not None and goal == here:
                # Already standing on it. An empty path means ARRIVED, not
                # unreachable — conflating the two made the agent conclude it
                # could not reach food it was standing on.
                c.status = DONE
                return c
            self._plan_to_target(target)
            if not self._path:
                c.status = FAILED
                c.reason = "no_path"
        return c

    def interrupt(self, reason: str = "interrupted") -> None:
        if self.busy():
            self.current.status = FAILED
            self.current.reason = reason

    # --- planning ---------------------------------------------------------
    def _believed_passable(self, cell) -> bool:
        if cell in self.wm.walls:
            return False
        # Cells we recently bumped into are treated as blocked even if the map
        # says otherwise. Without this the agent has no memory of colliding and
        # will re-plan the identical failing route forever — the single largest
        # source of wasted ticks before it was added.
        return self._blocked.get(cell, -1) < self.tick

    def _target_cell(self, target: str):
        b = self.wm.beliefs.get(target)
        if b is None:
            return None
        proj = self.wm.resolve(target, self.tick)
        pos = proj.pos if proj else b.pos
        cell = (int(round(pos[0])), int(round(pos[1])))
        if self._believed_passable(cell):
            return cell
        # The confabulated position may land inside a wall; walk to the nearest
        # believed-passable neighbour instead of failing outright.
        w, h = self.wm.bounds
        best, bestd = None, 1e9
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                c = (cell[0] + dx, cell[1] + dy)
                if not (0 <= c[0] < w and 0 <= c[1] < h):
                    continue
                if not self._believed_passable(c):
                    continue
                d = dx * dx + dy * dy
                if d < bestd:
                    best, bestd = c, d
        return best

    def _plan_to_target(self, target: str) -> None:
        goal = self._target_cell(target)
        if goal is None:
            self._path = []
            return
        start = (int(round(self.wm.pose[0])), int(round(self.wm.pose[1])))
        path = astar(start, goal, self._believed_passable, self.wm.bounds)
        self._path = path[1:] if path else []

    # --- per-tick ---------------------------------------------------------
    def step(self) -> Action:
        """Advance the commitment by one tick and emit the motor command."""
        c = self.current
        if c is None or c.status != RUNNING:
            return Action("wait")

        c.ticks += 1
        self.tick += 1
        if c.ticks > self.MAX_TICKS:
            c.status = FAILED
            c.reason = "timeout"
            return Action("wait")

        if c.verb == "wait":
            if c.ticks >= int(c.meta.get("duration", 10)):
                c.status = DONE
            return Action("wait")

        if c.verb == "eat":
            # Stays RUNNING for one tick; the world's verdict arrives via
            # note_result. Reporting DONE unconditionally meant the agent
            # "ate" 225 times while starving next to food it never stood on.
            return Action("eat")

        if c.verb == "goto":
            return self._step_goto(c)

        c.status = FAILED
        c.reason = f"unknown_verb:{c.verb}"
        return Action("wait")

    def _step_goto(self, c: Commitment) -> Action:
        here = (int(round(self.wm.pose[0])), int(round(self.wm.pose[1])))

        # Progress, not emission, clears the stuck counter. Resetting it every
        # time a move was *issued* meant a wedged agent could bump the same wall
        # indefinitely without the counter ever rising — a livelock that cost
        # 715 collisions in one run before it was caught.
        if here != self._last_here:
            self._stuck = 0
            self._last_here = here

        if self._stuck >= 3:
            # Believed pose disagrees with the world. Sidestep perpendicular to
            # the blocked direction to break the wedge; the resulting motion
            # usually brings a landmark into view and re-localizes us.
            self._path = []
            side = self._sidestep()
            if side:
                return Action("move", side)

        # Re-plan if belief moved the target or we drifted off the path.
        if not self._path:
            self._plan_to_target(c.target)
            if not self._path:
                c.status = FAILED
                c.reason = "no_path"
                return Action("wait")

        nxt = self._path[0]
        if nxt == here:
            self._path.pop(0)
            if not self._path:
                c.status = DONE
                return Action("wait")
            nxt = self._path[0]

        dx, dy = nxt[0] - here[0], nxt[1] - here[1]
        direction = None
        for name, (ddx, ddy) in DIRS.items():
            if (ddx, ddy) == (dx, dy):
                direction = name
                break
        if direction is None:
            # Belief drifted away from the plan; re-plan next tick.
            self._path = []
            self._stuck += 1
            if self._stuck > 12:
                c.status = FAILED
                c.reason = "lost"
            return Action("wait")

        self._last_dir = direction
        self._last_target_cell = nxt
        return Action("move", direction)

    def _sidestep(self) -> str | None:
        """Pick a direction perpendicular to the one that keeps failing."""
        d = self._last_dir
        opts = ["N", "S"] if d in ("E", "W") else ["E", "W"]
        here = (int(round(self.wm.pose[0])), int(round(self.wm.pose[1])))
        for o in opts:
            dx, dy = DIRS[o]
            cell = (here[0] + dx, here[1] + dy)
            if self._believed_passable(cell):
                return o
        return opts[0] if opts else None

    def note_result(self, blocked: bool, consumed: str | None = None) -> None:
        """The world's verdict on the last command. A blocked move where the
        agent believed the cell was clear means its POSE is wrong — so give up
        on this route rather than grinding, and let the policy pick something
        else that may restore localization."""
        if self.busy() and self.current.verb == "eat":
            if consumed:
                self.current.status = DONE
            else:
                self.current.status = FAILED
                self.current.reason = "nothing_here"
            return

        if blocked and self.busy():
            if self._last_target_cell is not None:
                # Remember the collision for a while so replanning routes around
                # it rather than retrying the same step.
                self._blocked[self._last_target_cell] = self.tick + 60
            self._path = []
            self._stuck += 1
            if self._stuck > 12:
                self.current.status = FAILED
                self.current.reason = "blocked"


def affordances(wm, frame_entities, tick: int) -> list[dict]:
    """What can actually be done right now, generated from perception.

    This is Gibsonian: the action space is produced by what is present, not
    enumerated in a prompt. It keeps the mind's choice set small and legal.
    """
    out: list[dict] = [{"verb": "wait"}]
    here = (int(round(wm.pose[0])), int(round(wm.pose[1])))
    for v in frame_entities:
        b = wm.beliefs.get(v.id)
        if b is None:
            continue
        cell = (int(round(b.pos[0])), int(round(b.pos[1])))
        if b.kind in ("food", "warmth", "item", "landmark", "critter", "resident"):
            out.append({"verb": "goto", "target": v.id})
        if b.kind == "food" and cell == here and b.state.get("available", True):
            out.append({"verb": "eat", "target": v.id})
    # Stable order so prompts (and caches) don't churn.
    out.sort(key=lambda a: (a["verb"], a.get("target", "")))
    return out
