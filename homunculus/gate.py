"""The surprise gate — when is it worth thinking?

This is the mechanism H1 is about: cognition is expensive, so consult the mind
only when something happened that the body cannot handle on its own.

Three things open the gate:
  1. the current commitment ended (there is nothing to do next),
  2. accumulated surprise crossed a threshold (the world is not as expected),
  3. a heartbeat has elapsed (never go fully blind).

The threshold is ADAPTIVE, controlled toward a target call rate, rather than a
hand-tuned constant. A fixed threshold silently decalibrates the moment the
world, the sensor model, or the drift model changes — and it would make H1 a
measurement of my tuning rather than of the architecture.

Surprise ACCUMULATES between calls: many small anomalies should eventually earn
a thought even if none alone would, and the accumulator resets on every call so
attention is spent, not merely detected.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GateStats:
    opened: int = 0
    by_reason: dict = field(default_factory=dict)
    closed_ticks: int = 0

    def note(self, reason: str) -> None:
        self.opened += 1
        self.by_reason[reason] = self.by_reason.get(reason, 0) + 1


class SurpriseGate:
    def __init__(self, target_calls_per_1k: float = 12.0, heartbeat: int = 400,
                 threshold: float = 3.0, adapt: bool = True,
                 allow_interrupt: bool = False, habit: bool = True):
        # Two ways surprise can gate cognition, and the direction matters:
        #
        #   allow_interrupt  surprise ABORTS the action in progress to think.
        #                    Measured net-negative here: abandoning in-flight
        #                    work costs more than reconsidering gains, and each
        #                    interrupt cascades into extra decisions. Off.
        #   habit            at a natural decision point, if nothing surprising
        #                    has happened, repeat the standing plan WITHOUT
        #                    consulting the mind. This is the System 1 / System 2
        #                    split, and it removes calls rather than adding them.
        self.allow_interrupt = allow_interrupt
        self.habit = habit
        self.target = target_calls_per_1k
        self.heartbeat = heartbeat
        self.threshold = threshold
        self.adapt = adapt
        self.accum = 0.0
        self.baseline = 0.0
        self.last_call_tick = 0
        self.stats = GateStats()
        self._window_start = 0
        self._window_surprise_calls = 0
        self.window = 500

    def observe(self, surprise_scalar: float) -> None:
        """Accumulate EXCESS surprise, relative to the running background.

        Accumulating raw surprise does not work: with a decay of 0.97 and a
        typical per-tick scalar near 1, the accumulator settles around 33 and
        any sane threshold fires every tick. What should earn a thought is
        deviation from what is normally surprising in this world, so the
        baseline is tracked and subtracted.
        """
        s = max(surprise_scalar, 0.0)
        self.baseline += 0.01 * (s - self.baseline)
        self.accum = self.accum * 0.9 + max(0.0, s - self.baseline)

    def should_open(self, tick: int, motor_busy: bool,
                    has_habit: bool = False) -> str | None:
        """Return a reason to consult the mind, or None to stay reflexive."""
        if motor_busy:
            if not self.allow_interrupt:
                return None
            if self.accum >= self.threshold:
                return "surprise"
            if tick - self.last_call_tick >= self.heartbeat:
                return "heartbeat"
            return None

        # The motor is idle: something must be chosen. The question is whether
        # it is worth THINKING about, or whether habit will do.
        if not self.habit or not has_habit:
            return "idle"
        if self.accum >= self.threshold:
            return "surprise"
        if tick - self.last_call_tick >= self.heartbeat:
            return "heartbeat"
        return None                      # act habitually, no call

    def opened(self, tick: int, reason: str) -> None:
        self.stats.note(reason)
        self.accum = 0.0                    # attention is spent, not just felt
        self.last_call_tick = tick
        if reason == "surprise":
            self._window_surprise_calls += 1
        if self.adapt and tick - self._window_start >= self.window:
            self._retune(tick)

    def _retune(self, tick: int) -> None:
        """Proportional control on the threshold toward the target call rate.

        Only SURPRISE-triggered calls are controllable — `idle` calls are
        structural (the body finished a task and needs another), so raising the
        threshold cannot suppress them. Controlling on the total would wind the
        threshold to infinity chasing a rate the gate does not govern.

        The correction is proportional to the ratio rather than a fixed step: a
        fixed 1.25x per window is far too sluggish to catch a gate firing 20x
        over budget, which is exactly what happened.
        """
        span = max(tick - self._window_start, 1)
        rate = self._window_surprise_calls * 1000.0 / span
        budget = max(self.target * 0.6, 0.5)      # surprise's share of the target
        if rate > budget:
            factor = min(max(rate / budget, 1.05), 4.0)
            self.threshold = min(self.threshold * factor, 1e4)
        elif rate < budget * 0.5:
            self.threshold = max(self.threshold * 0.8, 0.05)
        self._window_start = tick
        self._window_surprise_calls = 0


class Governor:
    """Smooths request rate.

    Together's limiter is dynamic, unpublished, and specifically penalises
    bursts — the exact pattern of an agent waking up and firing several calls at
    once. This enforces a minimum spacing between calls in wall-clock terms; it
    is a no-op for mock runs and for tick-discrete experiments, and matters the
    moment a real key is used.
    """

    def __init__(self, min_interval_s: float = 0.0, max_concurrent: int = 1):
        self.min_interval = min_interval_s
        self.max_concurrent = max_concurrent
        self._last = 0.0

    def acquire(self) -> None:
        if self.min_interval <= 0:
            return
        import time

        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
