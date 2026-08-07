"""The agent's BELIEF about the world.

Never reads ground truth. It holds:

  * a believed pose, advanced by efference copy (the agent's own motor command),
    which drifts from reality whenever a move is blocked by something unseen;
  * a landmark map (static features are known a priori — the agent lives here),
    used to re-localize and collapse accumulated pose error;
  * beliefs about everything else as FROZEN observation records that are never
    ticked forward, only confabulated lazily on read via dynamics.project().

The loop-closure detail that matters: observations are stored in allocentric
coordinates derived from the pose believed AT THE TIME, so a later landmark fix
retro-corrects every belief recorded since the previous fix. Without that, a
pose correction silently leaves a trail of systematically wrong beliefs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import dynamics as dyn
from .frame import EntityView
from .geometry import abs_bearing, dist, polar_to_offset, quantize, rel_bearing


@dataclass
class Belief:
    id: str
    kind: str
    pos: tuple[float, float]
    last_seen: int
    state: dict = field(default_factory=dict)
    vel: tuple[float, float] | None = None       # EMA net velocity, animate only
    track: int = 0                                # consecutive sightings (velocity quality)
    persistence: float = 0.3                      # learned: does its heading predict?
    fix_epoch: int = 0                            # which pose-fix era recorded it


class WorldModel:
    def __init__(self, start_pose, landmarks: dict[str, tuple[int, int]], walls=None,
                 bounds=None):
        self.pose = (float(start_pose[0]), float(start_pose[1]), float(start_pose[2]))
        self.pose_conf = 1.0
        self.landmarks = dict(landmarks)
        self.walls = set(walls or ())
        self.bounds = bounds
        self.beliefs: dict[str, Belief] = {}
        self.fix_epoch = 0
        self.last_fix_tick = 0
        self.traffic: dict[str, float] = {}       # room/area -> inferred activity

    # --- self-motion ------------------------------------------------------
    def predict_pose(self, action) -> None:
        """Advance believed pose from the motor command alone. The agent does
        not know whether the move succeeded — that is the point."""
        from .world import DIRS, HEADING

        if action is None or action.verb != "move" or action.dir not in DIRS:
            return
        dx, dy = DIRS[action.dir]
        x, y, _ = self.pose
        self.pose = (x + dx, y + dy, HEADING[action.dir])
        # Dead reckoning erodes confidence until a landmark fix restores it.
        self.pose_conf = max(0.0, self.pose_conf - 0.004)

    def apply_bump(self, action) -> None:
        """A bump is FELT. Proprioception tells the agent the move didn't happen,
        so it undoes its own predicted step. Collisions are therefore not a
        source of drift — real drift comes only from errors the body cannot
        detect (slips), which is why it accumulates silently until a landmark
        fix catches it."""
        from .world import DIRS

        if action is not None and action.verb == "move" and action.dir in DIRS:
            dx, dy = DIRS[action.dir]
            x, y, h = self.pose
            self.pose = (x - dx, y - dy, h)
        self.pose_conf = max(0.0, self.pose_conf - 0.01)

    # --- localization -----------------------------------------------------
    def _fix_from_landmarks(self, observations, tick: int) -> bool:
        """Re-localize from observed known landmarks.

        Two guards keep the fix from doing harm. Bearings are quantized, so a
        trilateration is itself noisy: we BLEND toward the estimate rather than
        snapping to it (more landmarks -> more weight), and we skip the move
        entirely when the agent is already confident and the disagreement is
        within sensor noise. A fix should never make a good estimate worse.
        """
        seen = [o for o in observations if o.id in self.landmarks]
        if not seen:
            return False
        xs, ys = [], []
        for o in seen:
            lx, ly = self.landmarks[o.id]
            # Invert the egocentric encoding using the believed heading.
            dx, dy = polar_to_offset(o.range, o.bearing, self.pose[2])
            xs.append(lx - dx)
            ys.append(ly - dy)
        est = (sum(xs) / len(xs), sum(ys) / len(ys))
        disagreement = math.hypot(est[0] - self.pose[0], est[1] - self.pose[1])

        self.last_fix_tick = tick
        if disagreement < 1.0 and self.pose_conf > 0.85:
            self.pose_conf = min(1.0, self.pose_conf + 0.05)
            return True                      # already right; don't inject noise

        alpha = min(0.85, 0.45 + 0.2 * len(seen))
        nx = self.pose[0] + alpha * (est[0] - self.pose[0])
        ny = self.pose[1] + alpha * (est[1] - self.pose[1])
        shift = (nx - self.pose[0], ny - self.pose[1])
        self.pose = (nx, ny, self.pose[2])
        self.pose_conf = min(1.0, 0.85 + 0.05 * len(seen))

        if math.hypot(*shift) > 0.75:
            # Loop closure: retro-correct beliefs recorded in the drifted era.
            for b in self.beliefs.values():
                if b.fix_epoch == self.fix_epoch and b.kind not in ("landmark", "wall"):
                    b.pos = (b.pos[0] + shift[0], b.pos[1] + shift[1])
            self.fix_epoch += 1
        return True

    # --- observation ------------------------------------------------------
    def ingest(self, observations, tick: int, events=None, action=None) -> None:
        for ev in events or ():
            if ev.get("kind") == "bump":
                self.apply_bump(action)

        self._fix_from_landmarks(observations, tick)

        px, py, ph = self.pose
        for o in observations:
            dx, dy = polar_to_offset(o.range, o.bearing, ph)
            pos = (px + dx, py + dy)
            prev = self.beliefs.get(o.id)
            vel = prev.vel if prev else None
            track = 0
            persistence = prev.persistence if prev else 0.3
            if prev is not None and dyn.class_of(o.kind) == "animate":
                vel = self._infer_intent(prev, pos, tick)
                # A velocity estimate is only trustworthy if it was built from
                # continuous observation; a sighting after a long gap resets it.
                track = prev.track + 1 if (tick - prev.last_seen) <= 2 else 0
            self.beliefs[o.id] = Belief(
                id=o.id, kind=o.kind, pos=pos, last_seen=tick,
                state=dict(o.state), vel=vel, track=track,
                persistence=persistence, fix_epoch=self.fix_epoch,
            )

    def learn_persistence(self, eid: str, rollout_err: float, baseline_err: float,
                          rate: float = 0.08) -> None:
        """Second-order learning: did this entity's heading actually predict it?

        Compares the rollout against the no-motion baseline on a real
        observation and nudges the entity's persistence accordingly. This is
        what lets one mechanism serve both a wanderer (persistence falls, the
        envelope tightens, rollout collapses to no-motion) and a resident with a
        routine (persistence rises, the envelope opens, motion is extrapolated).
        """
        b = self.beliefs.get(eid)
        if b is None:
            return
        better = baseline_err - rollout_err
        scale = max(baseline_err, rollout_err, 1e-6)
        signal = max(-1.0, min(1.0, better / scale))
        b.persistence = max(0.0, min(1.0, b.persistence + rate * signal))

        self._decay_traffic()

    def _infer_intent(self, prev: Belief, now_pos, tick: int):
        """Estimate net velocity, smoothed across sightings.

        A two-point difference between consecutive observations is dominated by
        quantization noise (both endpoints are rounded and pose-corrupted), which
        is what made the first version of rollout worse than assuming no motion.
        An EMA over sightings, with a longer baseline weighted more heavily,
        recovers the actual drift direction.
        """
        dt = max(tick - prev.last_seen, 1)
        vx = (now_pos[0] - prev.pos[0]) / dt
        vy = (now_pos[1] - prev.pos[1]) / dt
        # Trust a longer baseline more: one-tick gaps are mostly noise.
        alpha = min(0.6, 0.15 + 0.08 * dt)
        pv = prev.vel or (0.0, 0.0)
        return (pv[0] + alpha * (vx - pv[0]), pv[1] + alpha * (vy - pv[1]))

    # --- traffic (conditions inert_movable decay) -------------------------
    def note_traffic(self, key: str, amount: float = 1.0) -> None:
        self.traffic[key] = self.traffic.get(key, 0.0) + amount

    def _decay_traffic(self) -> None:
        for k in list(self.traffic):
            v = self.traffic[k] * 0.995
            if v < 1e-3:
                del self.traffic[k]
            else:
                self.traffic[k] = v

    def _traffic_near(self, pos) -> float:
        return self.traffic.get(self._area(pos), 0.0)

    @staticmethod
    def _area(pos) -> str:
        return f"{int(pos[0]) // 5},{int(pos[1]) // 5}"

    # --- the read path: lazy confabulation --------------------------------
    def resolve(self, eid: str, tick: int) -> dyn.Projection | None:
        """Confabulate the current state of one entity. Pure function of the
        frozen record and elapsed time — nothing here mutates."""
        b = self.beliefs.get(eid)
        if b is None:
            return None
        d = dyn.for_kind(b.kind)
        dt = max(tick - b.last_seen, 0)
        ctx = {"bounds": self.bounds, "persistence": b.persistence}
        if b.vel:
            ctx["vel"] = b.vel
        return d.project(b, dt, traffic=self._traffic_near(b.pos), ctx=ctx)

    def entity_views(self, tick: int, observed_ids: set[str]) -> list[EntityView]:
        """Render every belief into the Frame's egocentric polar form."""
        px, py, ph = self.pose
        views: list[EntityView] = []
        for eid in sorted(self.beliefs):
            b = self.beliefs[eid]
            proj = self.resolve(eid, tick)
            if proj is None:
                continue
            age = tick - b.last_seen
            seen_now = eid in observed_ids
            if not seen_now and proj.conf < 0.05:
                continue                     # forgotten; falls out of the Frame
            r = dist((px, py), proj.pos)
            qr, qb = quantize(r, rel_bearing(abs_bearing((px, py), proj.pos), ph))
            hyps = []
            if not seen_now and proj.hypotheses:
                for (hx, hy), wgt in proj.hypotheses:
                    if wgt < 0.05:
                        continue
                    hr = dist((px, py), (hx, hy))
                    hqr, hqb = quantize(hr, rel_bearing(abs_bearing((px, py), (hx, hy)), ph))
                    hyps.append({"bearing": hqb, "range": hqr, "w": round(wgt, 3)})
                hyps.sort(key=lambda h: -h["w"])
            views.append(EntityView(
                id=eid, kind=b.kind, bearing=qb, range=qr,
                conf=1.0 if seen_now else proj.conf,
                age=age, observed=seen_now, state=dict(b.state),
                hypotheses=hyps[:3],
            ))
        return views
