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
from .geometry import polar_to_offset, quantization_var


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


class DriftModel:
    """Online per-class calibration. Tracks mean normalized error; a class that
    consistently exceeds 1.0 is overconfident and gets its assumed diffusion
    scaled up. Cheap second-order learning, no training loop."""

    def __init__(self):
        self.stats: dict[str, list[float]] = defaultdict(list)
        self.scale: dict[str, float] = defaultdict(lambda: 1.0)

    def record(self, cls: str, normalized: float) -> None:
        s = self.stats[cls]
        s.append(normalized)
        if len(s) > 400:
            del s[:200]

    def update(self) -> None:
        for cls, vals in self.stats.items():
            if len(vals) < 40:
                continue
            mean = sum(vals) / len(vals)
            if mean > 1.3:
                self.scale[cls] = min(self.scale[cls] * 1.05, 8.0)
            elif mean < 0.7:
                self.scale[cls] = max(self.scale[cls] * 0.97, 0.25)

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
        var = (d.expected_error(dt) * self.scale[dyn.class_of(kind)]) ** 2
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
        drift.record(dyn.class_of(b.kind), normalized)
        # Every re-sighting is a labelled example of whether this entity's
        # heading predicts it. Only meaningful once some time has elapsed.
        if dt >= 3 and dyn.class_of(b.kind) == "animate":
            wm.learn_persistence(oid, raw, baseline)

        if b.state.get("available") != o.state.get("available"):
            rep.existence.append(f"~{oid}")

    # Things we confidently expected to see here and did not.
    for bid in sorted(wm.beliefs):
        if bid in obs_by_id:
            continue
        proj = wm.resolve(bid, tick)
        if proj is None or proj.conf < 0.6:
            continue
        r = math.hypot(proj.pos[0] - px, proj.pos[1] - py)
        if r <= 4.0:                      # should have been comfortably in view
            rep.existence.append(f"-{bid}")

    vals = list(rep.spatial.values())
    spatial_term = max(vals) if vals else 0.0
    rep.scalar = spatial_term + 1.5 * len(rep.existence) + 1.0 * len(rep.events)
    drift.update()
    return rep
