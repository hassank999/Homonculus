"""Per-class dynamics: how a belief decays when nobody is looking.

This is the confabulation engine (PLAN.md §4.2, elaborated in the design notes).
`project` is a PURE function of (record, elapsed, class) — that purity is what
makes drift auditable and time-travelable: you can ask what the agent would have
believed at tick N without having run to tick N, and two runs from the same seed
produce identical beliefs.

Three archetypes:
  static          walls/landmarks — position never drifts, confidence -> high floor
  inert_movable   cups/items — position holds, CONFIDENCE decays, and the decay
                  is conditioned on inferred traffic (an item in a busy room
                  becomes unknown faster than one in an empty room)
  animate         agents/critters — NOT diffusion but ROLLOUT along inferred
                  intent, returning ranked hypotheses rather than one point
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Projection:
    pos: tuple[float, float]
    var: float                    # positional variance (units^2)
    conf: float                   # 0..1 belief that this is still true
    hypotheses: tuple = ()        # ranked ((x, y), weight) for animate kinds

    @property
    def sigma(self) -> float:
        return math.sqrt(max(self.var, 1e-9))


class Dynamics:
    """Base: a thing that does not move and is slowly forgotten."""

    tau = 4000.0        # confidence half-life-ish constant (ticks)
    floor = 0.9         # asymptotic confidence
    diffusion = 0.0     # variance growth per tick
    var0 = 0.02         # observation variance at the moment of sighting

    def confidence(self, dt: int, traffic: float = 0.0) -> float:
        tau = self.tau / (1.0 + max(traffic, 0.0))
        return self.floor + (1.0 - self.floor) * math.exp(-dt / max(tau, 1e-9))

    def project(self, belief, dt: int, traffic: float = 0.0, ctx=None) -> Projection:
        return Projection(
            pos=belief.pos,
            var=self.var0 + self.diffusion * dt,
            conf=self.confidence(dt, traffic),
        )

    def expected_error(self, dt: int) -> float:
        """Predicted magnitude of positional error after dt ticks. This is the
        denominator that turns raw error into normalized surprise."""
        return math.sqrt(self.var0 + self.diffusion * dt)


class StaticDynamics(Dynamics):
    tau, floor, diffusion, var0 = 20000.0, 0.95, 0.0, 0.01


class InertMovableDynamics(Dynamics):
    """Doesn't move itself; gets relocated by agents. Position barely drifts,
    but confidence decays — faster where traffic is inferred."""

    tau, floor, diffusion, var0 = 600.0, 0.15, 0.004, 0.05


class AnimateDynamics(Dynamics):
    """Rollout along inferred intent — bounded by how far a wanderer can get.

    The naive version of this (extrapolate observed velocity ballistically) is
    substantially WORSE than assuming the entity stayed put, for two reasons
    measured directly in this sim:

      * at short dt the observed velocity is dominated by quantization noise, so
        extrapolating it flings the estimate off in a random direction;
      * a bounded wanderer's NET displacement saturates — it tours a room rather
        than departing in a straight line — so long-horizon ballistic
        extrapolation leaves the map entirely.

    So travel is capped by a saturating envelope R(1-e^(-dt/T)), which makes the
    rollout decay gracefully into the no-motion estimate at long horizons. A
    predictor should never do worse than the naive baseline it replaces.
    """

    tau, floor, diffusion, var0 = 120.0, 0.02, 0.06, 0.08
    max_speed = 1.0      # cap on estimated net drift rate

    # The displacement envelope is NOT a global constant — it is set by the
    # entity's learned `persistence` (see worldmodel.Belief). A wanderer earns a
    # tight envelope, so its rollout collapses toward the no-motion answer; a
    # resident with a routine earns a loose one and is predicted ballistically.
    # One constant cannot serve both, which is why this is learned per entity.
    def envelope(self, persistence: float) -> tuple[float, float]:
        r_sat = 1.5 + 14.0 * persistence
        t_sat = 20.0 + 200.0 * persistence
        return r_sat, t_sat

    def max_disp(self, dt: int, persistence: float = 0.3) -> float:
        r_sat, t_sat = self.envelope(persistence)
        return r_sat * (1.0 - math.exp(-dt / t_sat))

    def project(self, belief, dt: int, traffic: float = 0.0, ctx=None) -> Projection:
        ctx = ctx or {}
        start = belief.pos
        conf = self.confidence(dt, traffic)
        var = self.var0 + self.diffusion * dt
        vel = ctx.get("vel")
        bounds = ctx.get("bounds")
        persistence = float(ctx.get("persistence", 0.3))
        _, t_sat = self.envelope(persistence)

        def clamp(p):
            if not bounds:
                return p
            w, h = bounds
            return (min(max(p[0], 1.0), w - 2.0), min(max(p[1], 1.0), h - 2.0))

        speed = math.hypot(*vel) if vel else 0.0
        if not vel or speed < 1e-6 or dt <= 0:
            return Projection(pos=start, var=var, conf=conf,
                              hypotheses=(((round(start[0], 3), round(start[1], 3)), 1.0),))

        speed = min(speed, self.max_speed)
        travel = min(speed * dt, self.max_disp(dt, persistence))
        ux, uy = vel[0] / math.hypot(*vel), vel[1] / math.hypot(*vel)
        primary = clamp((start[0] + ux * travel, start[1] + uy * travel))

        # How much to trust the heading also scales with learned persistence.
        p_head = (0.25 + 0.7 * persistence) * math.exp(-dt / t_sat)
        p_stay = 1.0 - p_head
        hyps = (
            ((round(primary[0], 3), round(primary[1], 3)), round(p_head, 3)),
            ((round(start[0], 3), round(start[1], 3)), round(p_stay, 3)),
        )
        # Point estimate is the weighted blend, so it interpolates smoothly from
        # rollout (short dt) to no-motion (long dt).
        px = primary[0] * p_head + start[0] * p_stay
        py = primary[1] * p_head + start[1] * p_stay
        return Projection(pos=clamp((px, py)), var=var, conf=conf, hypotheses=hyps)


_REGISTRY: dict[str, Dynamics] = {
    "static": StaticDynamics(),
    "inert_movable": InertMovableDynamics(),
    "animate": AnimateDynamics(),
}

# Entity kind -> dynamics class.
KIND_CLASS: dict[str, str] = {
    "wall": "static",
    "landmark": "static",
    "food": "inert_movable",
    "item": "inert_movable",
    "warmth": "static",
    "critter": "animate",
    "resident": "animate",
    "agent": "animate",
}


def for_kind(kind: str) -> Dynamics:
    return _REGISTRY[KIND_CLASS.get(kind, "inert_movable")]


def class_of(kind: str) -> str:
    return KIND_CLASS.get(kind, "inert_movable")
