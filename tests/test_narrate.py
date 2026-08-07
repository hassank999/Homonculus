"""Tests for the conscious stream.

The stream is the readable account of a run: what woke the agent, what it felt,
what it could see, what it chose and why, and how that turned out. It is not
decoration — rendering it immediately exposed two behavioural bugs that the
aggregate metrics had hidden, both regressed against here.
"""

from __future__ import annotations

from homunculus.gate import SurpriseGate
from homunculus.loop import Runtime, _run_start_event
from homunculus.memory import Memory
from homunculus.mind import Mind
from homunculus.narrate import stream, to_text
from homunculus.provider import MockProvider


def _run(seed=42, ticks=2000):
    rt = Runtime(seed, policy=Mind(MockProvider()), gate=SurpriseGate(),
                 memory=Memory(), sleep_every=800)
    evs = [_run_start_event(seed, "apartment", rt.world, ticks, "mind")]
    for _ in range(ticks):
        evs.append(rt.step())
    return evs


def test_stream_covers_the_causal_chain():
    s = stream(_run())
    kinds = {e["kind"] for e in s}
    assert "thought" in kinds, "no deliberation recorded"
    assert "outcome" in kinds, "intentions never resolved"
    assert {"habit", "percept"} & kinds, "no reflexive or sensed events"

    thoughts = [e for e in s if e["kind"] == "thought"]
    assert thoughts
    # A thought must say why it happened, what was seen, and what was thought.
    assert any(e["body"] for e in thoughts), "no thought explains its trigger"
    assert any(e.get("saw") for e in thoughts), "no thought records perception"
    assert any(e.get("note") for e in thoughts), "the mind never explains itself"


def test_thoughts_are_ordered_and_within_the_run():
    evs = _run()
    s = stream(evs)
    ticks = [e["t"] for e in s]
    assert ticks == sorted(ticks), "stream is out of order"
    assert max(ticks) <= 2000


def test_habits_are_distinguished_from_thoughts():
    """The whole point of the gate is that most action is unthinking; the
    stream must show which is which or it misrepresents the architecture."""
    s = stream(_run())
    habits = [e for e in s if e["kind"] == "habit"]
    thoughts = [e for e in s if e["kind"] == "thought"]
    assert habits and thoughts
    assert all(not h.get("note") for h in habits), "habits should not claim reasoning"


def test_agent_does_not_walk_to_where_it_already_stands():
    """Regression: the stream revealed the agent repeatedly 'setting off
    toward' something underfoot and arriving in zero ticks."""
    s = stream(_run())
    zero_arrivals = [
        e for e in s
        if e["kind"] == "outcome" and "after 0 ticks" in e["head"]
    ]
    assert len(zero_arrivals) < 5, f"{len(zero_arrivals)} pointless zero-tick trips"


def test_epistemic_action_prefers_lasting_information():
    """Regression: most decisions were spent chasing critters, which cannot be
    pinned down. Checking something that stays put is worth the trip; chasing
    something that moves is not."""
    evs = _run()
    chases = 0
    for ev in evs:
        d = ev.get("decision")
        if d and d.get("verb") == "goto" and str(d.get("target", "")).startswith("c"):
            chases += 1
    decisions = sum(1 for ev in evs if ev.get("decision"))
    assert decisions > 0
    assert chases / decisions < 0.75, "still mostly chasing moving targets"


def test_text_rendering_is_readable():
    s = stream(_run(ticks=800))
    txt = to_text(s, limit=40)
    assert txt and "\n" in txt
    assert any(w in txt for w in ("set off", "arrived", "ate", "waited", "gave up"))


def test_stream_handles_an_empty_run():
    assert stream([]) == []
    assert to_text([]) == ""
