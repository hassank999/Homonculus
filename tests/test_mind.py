"""P3 exit tests — the LLM mind, the surprise gate, and H1.

H1: surprise-gated cognition cuts LLM calls by >=50x versus deliberating every
tick, without degrading behaviour. Measured on identical seeds with only the
gating changed.

The task score is body-based (how near the drives stayed to their setpoints) so
the gate cannot game it by thinking less about a metric it also controls.

These tests use MockProvider throughout: the full loop, prompt assembly, schema
validation and parse path all execute, with no key, no network, and no spend.
"""

from __future__ import annotations

import json

from homunculus.experiments import h1, run_condition
from homunculus.gate import SurpriseGate
from homunculus.mind import SCHEMA, Mind
from homunculus.provider import Completion, MockProvider, Usage

SEEDS = (1, 2, 3, 11, 42, 7)


def test_h1_gating_cuts_calls_without_degrading_behaviour():
    """The headline claim. Threshold is 50x per PLAN.md; degradation limit 10%."""
    _rows, s = h1(seeds=SEEDS, ticks=5000)
    reduction = s["gated"]["reduction_x"]
    delta = s["gated"]["score_delta_pct"]
    assert reduction >= 50.0, f"only {reduction:.1f}x fewer calls than every-tick"
    assert delta > -10.0, f"behaviour degraded {delta:.1f}% versus every-tick"


def test_durative_action_carries_most_of_the_saving():
    """Honest decomposition: the gate should not be credited with the motor's
    work. Most of the reduction comes from actions lasting many ticks."""
    _rows, s = h1(seeds=(1, 2, 3), ticks=3000)
    assert s["idle_only"]["reduction_x"] > 10.0
    assert s["gated"]["reduction_x"] > s["idle_only"]["reduction_x"]


def test_thinking_every_tick_is_not_better():
    """Constant reconsideration prevents anything from being completed."""
    _rows, s = h1(seeds=(1, 2, 3), ticks=3000)
    assert s["every_tick"]["score"] <= s["gated"]["score"] + 0.05


def test_gate_acts_habitually_when_unsurprised():
    """The gate must REMOVE calls at decision points, not add interrupts."""
    r = run_condition(11, 4000, "gated", MockProvider())
    assert r["calls"] < 250, "gate is not suppressing routine decisions"
    assert r["gate_reasons"], "gate never opened at all"


def test_gate_threshold_adapts_toward_target():
    g = SurpriseGate(target_calls_per_1k=5.0, threshold=1.0)
    start = g.threshold
    for tick in range(1, 4001):
        g.observe(8.0)                       # relentless surprise
        if g.should_open(tick, motor_busy=False, has_habit=True):
            g.opened(tick, "surprise")
    assert g.threshold > start, "threshold never rose against constant surprise"


def test_surprise_accumulates_relative_to_background():
    """Regression: accumulating raw surprise saturated at ~33 and fired every
    tick regardless of threshold. Only EXCESS over the background counts."""
    g = SurpriseGate(threshold=5.0)
    for _ in range(500):
        g.observe(1.0)                       # steady, unremarkable
    assert g.accum < 5.0, f"steady background saturated accumulator ({g.accum:.1f})"
    for _ in range(6):
        g.observe(25.0)                      # a real anomaly
    assert g.accum >= 5.0, "a genuine spike failed to open the gate"


def test_mind_parses_structured_output():
    mind = Mind(MockProvider())
    r = run_condition(3, 400, "gated", MockProvider())
    assert r["errors"] == 0
    assert "verb" in json.dumps(SCHEMA)


def test_mind_falls_back_when_provider_fails():
    """A dead tick is worse than a dull one: a provider error must still yield
    a legal action rather than stalling the body."""

    class Broken:
        name, model = "broken", "broken/x"

        def complete(self, system, user, schema):
            return Completion({}, Usage(), "", error="boom")

    from homunculus.loop import Runtime

    mind = Mind(Broken())
    rt = Runtime(5, policy=mind, gate=SurpriseGate())
    for _ in range(300):
        rt.step()
    assert mind.errors > 0, "expected provider errors"
    assert rt.motor.current is not None, "body stalled on provider failure"


def test_mind_rejects_illegal_targets():
    """A hallucinated target is an error path, not something handed to the
    motor to fail on later."""

    class Liar:
        name, model = "liar", "liar/x"

        def complete(self, system, user, schema):
            return Completion(
                {"action": {"verb": "goto", "target": "nonexistent_thing"}},
                Usage(), "{}",
            )

    from homunculus.loop import Runtime

    mind = Mind(Liar())
    rt = Runtime(5, policy=mind, gate=SurpriseGate())
    for _ in range(200):
        rt.step()
    assert mind.errors > 0, "illegal target was accepted"


def test_system_prompt_is_byte_stable():
    """Together bills the longest matching prefix at the cached rate, so the
    fixed half must be identical every call — no timestamps, no counters."""
    a, b = Mind.system_prompt(), Mind.system_prompt()
    assert a == b
    for token in ("tick", "0x", "seed"):
        assert token not in a.lower().split("\n")[0]


def test_cost_accounting_tracks_cached_tokens():
    u = Usage(prompt_tokens=2000, cached_tokens=1800, completion_tokens=400)
    full = Usage(prompt_tokens=2000, cached_tokens=0, completion_tokens=400)
    model = "zai-org/GLM-5.2"
    assert u.cost(model) < full.cost(model), "caching must reduce cost"
