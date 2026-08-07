"""Prediction error -> the one signal that runs the architecture.

THE detail that everything depends on: raw error is not surprise. If you haven't
looked at something in 10,000 ticks, being wrong about it is *expected* and must
not earn a memory write. Surprise is error relative to the uncertainty you
already had:

    surprise = raw_error / expected_error(class, dt)

Skip the normalization and the episodic store fills with "the world changed
while I wasn't looking", which is noise — and H2 fails for reasons that have
nothing to do with the hypothesis.

Every correction is also a labelled data point (class, dt, normalized_error),
which is what lets the drift model tune itself: if a class's mean normalized
error sits above 1, the agent was systematically overconfident about it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from . import dynamics as dyn
from .geometry import polar_to_offset, quantization_var, visible


@dataclass
class SurpriseReport:
    tick: int
    scalar: float = 0.0
    spatial: dict = field(default_factory=dict)   # entity id -> normalized error
    existence: list = field(default_factory=list)  # ids present/absent unexpectedly
    events: list = field(default_factory=list)
    samples: list = field(default_factory=list)    # (class, dt, raw, expected)

    def top(self, n: int = 3):
        return sorted(self.spatial.items(), key=lambda kv: -kv[1])[:n]

    def to_dict(self) -> dict:
        return {
            "scalar": round(self.scalar, 4),
            "spatial": {k: round(v, 3) for k, v in sorted(self.spatial.items())},
            "existence": sorted(self.existence),
            "events": self.events,
        }


def dt_bucket(dt: int) -> int:
    """Elapsed-time band. Calibration must be tracked per band, not just per
    class: an entity can be well-modelled at short gaps and badly modelled at
    long ones, and a single per-class scale is structurally blind to that —
    short-gap samples vastly outnumber long-gap ones and drown the signal."""
    if dt <= 2:
        return 0
    if dt <= 10:
        return 1
    if dt <= 50:
        return 2
    return 3


class DriftModel:
    """Online calibration, tracked per (class, elapsed-time band).

    A band whose mean normalized error sits above 1 was overconfident, so its
    assumed spread is scaled up; below 1, scaled down. Cheap second-order
    learning with no training loop — the agent tunes its own expectations from
    the errors it actually makes.
    """

    def __init__(self):
        self.stats: dict[tuple[str, int], list[float]] = defaultdict(list)
        self.scale: dict[tuple[str, int], float] = defaultdict(lambda: 1.0)

    def record(self, cls: str, dt: int, normalized: float) -> None:
        s = self.stats[(cls, dt_bucket(dt))]
        s.append(normalized)
        if len(s) > 400:
            del s[:200]

    def update(self) -> None:
        for key, vals in self.stats.items():
            if len(vals) < 25:
                continue
            mean = sum(vals) / len(vals)
            if mean > 1.15:
                self.scale[key] = min(self.scale[key] * 1.06, 12.0)
            elif mean < 0.8:
                self.scale[key] = max(self.scale[key] * 0.97, 0.2)

    def expected(self, kind: str, dt: int, pose_var: float = 0.0,
                 obs_var: float = 0.0) -> float:
        """Expected positional error combines three independent sources:

            it moved (class dynamics) + I moved wrongly (pose drift)
                                      + my senses are coarse (quantization)

        Omitting the last two makes static entities look wildly surprising, when
        in fact the discrepancy is the agent's own error being attributed to the
        world. Variances add; the scale factor is the learned correction.
        """
        d = dyn.for_kind(kind)
        key = (dyn.class_of(kind), dt_bucket(dt))
        var = (d.expected_error(dt) * self.scale[key]) ** 2
        return math.sqrt(var + pose_var + obs_var)


def compute(wm, observations, tick: int, drift: DriftModel | None = None) -> SurpriseReport:
    """Compare what the agent expected to perceive against what arrived."""
    drift = drift or DriftModel()
    rep = SurpriseReport(tick=tick)
    px, py, ph = wm.pose

    obs_by_id = {o.id: o for o in observations}
    # The agent's own uncertainty is part of what it should expect to be wrong by.
    pose_var = (1.0 - wm.pose_conf) * 2.0 + 0.15

    for oid, o in sorted(obs_by_id.items()):
        b = wm.beliefs.get(oid)
        dx, dy = polar_to_offset(o.range, o.bearing, ph)
        actual = (px + dx, py + dy)

        if b is None:
            # Never seen before: novel, but not a prediction failure.
            rep.existence.append(f"+{oid}")
            rep.spatial[oid] = 1.0
            continue

        proj = wm.resolve(oid, tick)
        dt = max(tick - b.last_seen, 0)
        raw = math.hypot(actual[0] - proj.pos[0], actual[1] - proj.pos[1])
        expected = max(
            drift.expected(b.kind, dt, pose_var, quantization_var(o.range)), 1e-6
        )
        normalized = raw / expected
        rep.spatial[oid] = normalized
        # Baseline for H4: what a no-motion ("it stayed put") prediction would
        # have scored on this same observation. Recording it inline means the
        # rollout-vs-diffusion comparison uses identical samples.
        baseline = math.hypot(actual[0] - b.pos[0], actual[1] - b.pos[1])
        rep.samples.append(
            (dyn.class_of(b.kind), dt, raw, expected, baseline, b.track, b.kind,
             b.persistence)
        )
        drift.record(dyn.class_of(b.kind), dt, normalized)
        # Every re-sighting is a labelled example of whether this entity's
        # heading predicts it. Only meaningful once some time has elapsed.
        if dt >= 3 and dyn.class_of(b.kind) == "animate":
            wm.learn_persistence(oid, raw, baseline)

        if b.state.get("available") != o.state.get("available"):
            rep.existence.append(f"~{oid}")

    # Things we confidently expected to see here and did not.
    #
    # This must require LINE OF SIGHT. Occlusion means "I could not see it", not
    # "it is not there" — treating the two the same made the agent disconfirm
    # food behind a wall and then starve because it no longer believed food
    # existed anywhere.
    here_cell = (int(round(px)), int(round(py)))
    for bid in sorted(wm.beliefs):
        if bid in obs_by_id:
            continue
        proj = wm.resolve(bid, tick)
        if proj is None or proj.conf < 0.6:
            continue
        r = math.hypot(proj.pos[0] - px, proj.pos[1] - py)
        if r > 4.0:                       # too far to conclude anything
            continue
        cell = (int(round(proj.pos[0])), int(round(proj.pos[1])))
        if not visible(here_cell, cell, wm.walls):
            continue                      # blocked view: learn nothing
        rep.existence.append(f"-{bid}")

    vals = list(rep.spatial.values())
    spatial_term = max(vals) if vals else 0.0
    rep.scalar = spatial_term + 1.5 * len(rep.existence) + 1.0 * len(rep.events)
    drift.update()
    return rep
