"""P4 exit tests — memory, retrieval, and consolidation (H2).

H2: surprise-weighted consolidation preserves more answerable memories than
recency-only or random retention, under a bounded store.

Methodology note that matters: probe events are chosen on an OBJECTIVE
criterion (food consumed, entities appearing or vanishing), never by ranking on
the surprise score itself. Ranking probes by surprise would make the test
circular — the surprise policy retains precisely the items the probe then asks
about. An earlier version of this experiment did exactly that and reported a
14x win; the honest number is smaller.
"""

from __future__ import annotations

from homunculus.experiments import h2
from homunculus.memory import EpisodicStore, Memory


def test_h2_surprise_retention_beats_recency_and_random():
    res = h2(seeds=(1, 2, 3, 11, 42), ticks=6000, capacity=60)
    assert res["surprise"] > res["recency"], res
    assert res["surprise"] > res["random"], res
    assert res["surprise"] > 0.08, f"surprise recall implausibly low: {res}"


def test_h2_advantage_narrows_as_capacity_grows():
    """Sanity on the mechanism: with room for everything, policy matters less.
    If the gap did NOT narrow, the result would more likely be an artefact."""
    tight = h2(seeds=(1, 2, 3), ticks=6000, capacity=40)
    loose = h2(seeds=(1, 2, 3), ticks=6000, capacity=200)
    tight_gap = tight["surprise"] - max(tight["recency"], tight["random"])
    loose_gap = loose["surprise"] - max(loose["recency"], loose["random"])
    assert tight_gap >= loose_gap - 0.05


def test_eviction_respects_policy():
    surp = EpisodicStore(capacity=3, policy="surprise")
    for i in range(6):
        surp.add(tick=i, text=f"e{i}", surprise=float(i))
    kept = sorted(e.surprise for e in surp.items)
    assert kept == [3.0, 4.0, 5.0], kept

    rec = EpisodicStore(capacity=3, policy="recency")
    for i in range(6):
        rec.add(tick=i, text=f"e{i}", surprise=float(5 - i))
    assert sorted(e.tick for e in rec.items) == [3, 4, 5]


def test_retrieval_scores_relevance_recency_and_surprise():
    s = EpisodicStore(capacity=50, policy="surprise")
    s.add(10, "ate food_a near lm_pillar", 5.0, ("food_a",))
    s.add(20, "blocked near wall", 0.5, ())
    s.add(30, "cup missing near lm_corner", 4.0, ("cup",))
    got = s.retrieve("cup missing", tick=35, k=1)
    assert got and "cup" in got[0].text


def test_retrieval_marks_use():
    s = EpisodicStore(capacity=10, policy="surprise")
    ep = s.add(1, "ate food_b", 3.0, ("food_b",))
    s.retrieve("food_b", tick=2, k=1)
    assert ep.retrievals >= 1


def test_working_memory_is_bounded_and_verbatim():
    from homunculus.gate import SurpriseGate
    from homunculus.loop import Runtime
    from homunculus.mind import Mind
    from homunculus.provider import MockProvider

    mem = Memory(capacity=50, working=12)
    rt = Runtime(3, policy=Mind(MockProvider()), gate=SurpriseGate(), memory=mem)
    for _ in range(400):
        rt.step()
    assert len(mem.working) == 12
    assert all("tick" in r for r in mem.working)
    ticks = [r["tick"] for r in mem.working]
    assert ticks == sorted(ticks), "working memory must stay in order"


def test_sleep_distils_semantic_facts():
    """Consolidation must produce something the episodic log did not already
    state — a fact supported by repetition, not a copy of one episode."""
    mem = Memory(capacity=200)
    for i in range(12):
        mem.episodic.add(100 + i, f"t{i} cup missing", 3.0, ("cup",))
    out = mem.sleep(tick=500)
    assert out["facts"] >= 1
    assert any("cup" in f.text for f in mem.semantic.top())
    assert mem.semantic.top()[0].support >= 1


def test_procedural_store_learns_what_worked():
    mem = Memory()
    for _ in range(5):
        mem.procedural.record("hungry", "goto", "food_a", success=True)
    for _ in range(5):
        mem.procedural.record("hungry", "goto", "cup", success=False)
    best = mem.procedural.best("hungry")
    assert best is not None and best[2] == "food_a"


def test_memory_write_is_selective():
    """A tick that went exactly as predicted teaches nothing and must not be
    stored, or the bounded store fills with noise."""
    from homunculus.gate import SurpriseGate
    from homunculus.loop import Runtime
    from homunculus.mind import Mind
    from homunculus.provider import MockProvider

    mem = Memory(capacity=100_000)
    rt = Runtime(7, policy=Mind(MockProvider()), gate=SurpriseGate(), memory=mem)
    for _ in range(2000):
        rt.step()
    assert len(mem.episodic.items) < 2000, "stored every tick indiscriminately"
    assert len(mem.episodic.items) > 0, "stored nothing at all"
