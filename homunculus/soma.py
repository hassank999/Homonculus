"""Interoception: homeostatic drives, and the affect computed from them.

This is what makes anything MATTER. Without drives, "my body feels cold" is a
token with no consequences and the agent is only role-playing wanting things.
With them, every goal bottoms out in a scalar the world can actually perturb.

Affect is not injected — it is computed:
    valence = signed drive error   (are things good or bad?)
    arousal = magnitude + rate     (how urgently?)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Drive:
    name: str
    value: float
    setpoint: float
    drift: float                 # per-tick movement when unattended
    weight: float = 1.0          # relative importance in affect
    lo: float = 0.0
    hi: float = 1.0

    @property
    def error(self) -> float:
        """Signed: negative means below setpoint (deficit)."""
        return self.value - self.setpoint

    def tick(self) -> None:
        self.value = min(self.hi, max(self.lo, self.value + self.drift))

    def nudge(self, amount: float) -> None:
        self.value = min(self.hi, max(self.lo, self.value + amount))


class Soma:
    """The body. Drives move on their own; the world can perturb them."""

    def __init__(self):
        # Rates are calibrated so that a COMPETENT policy can hold homeostasis
        # while an inattentive one cannot. A world where even optimal play
        # starves would not test whether drives ground behaviour — it would only
        # measure how fast the agent dies.
        self.drives: dict[str, Drive] = {
            # Energy falls steadily and is restored by eating.
            "energy": Drive("energy", 0.8, 0.75, -0.00035, weight=1.2),
            # Warmth falls in the open and rises near a heat source.
            "warmth": Drive("warmth", 0.7, 0.70, -0.00025, weight=1.0),
            # Fatigue accumulates with movement and falls when resting.
            # Setpoint is not zero: some tiredness is the normal state of an
            # agent that is actually doing things.
            "fatigue": Drive("fatigue", 0.3, 0.40, +0.00030, weight=0.8),
        }
        self._prev_valence = 0.0

    # --- update -----------------------------------------------------------
    def step(self, *, near_food_eaten: bool, near_warmth: bool, moved: bool) -> None:
        for d in self.drives.values():
            d.tick()
        if near_food_eaten:
            self.drives["energy"].nudge(+0.50)
        if near_warmth:
            self.drives["warmth"].nudge(+0.020)
        self.drives["fatigue"].nudge(+0.0008 if moved else -0.0030)

    # --- affect -----------------------------------------------------------
    @property
    def valence(self) -> float:
        """Signed wellbeing. Fatigue is inverted: above setpoint is bad."""
        total = 0.0
        wsum = 0.0
        for d in self.drives.values():
            e = -d.error if d.name == "fatigue" else d.error
            total += d.weight * max(-1.0, min(1.0, e / 0.5))
            wsum += d.weight
        return total / max(wsum, 1e-9)

    @property
    def arousal(self) -> float:
        mag = sum(abs(d.error) * d.weight for d in self.drives.values())
        rate = abs(self.valence - self._prev_valence) * 20.0
        return min(1.0, mag + rate)

    def latch(self) -> None:
        """Call once per tick after reading affect, so rate-of-change is real."""
        self._prev_valence = self.valence

    def worst(self) -> tuple[str, float]:
        """The drive most in deficit — what the agent should care about now."""
        scored = []
        for d in self.drives.values():
            e = -d.error if d.name == "fatigue" else d.error
            scored.append((d.name, e * d.weight))
        scored.sort(key=lambda kv: kv[1])
        return scored[0]

    def to_dict(self) -> dict:
        return {n: round(d.value, 3) for n, d in sorted(self.drives.items())}
