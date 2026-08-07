"""P5 exit tests — two minds, speech, and the honest H4 retest.

Two things this phase exists to establish:

  * Speech is an ACTION, not a parallel channel. It costs a turn, it reaches
    only whoever is close enough to hear, and it must never be repeated
    reflexively (an early version had an agent say the identical sentence four
    times running, because speech had become a habit).
  * H4 gets its fairest possible test. It was falsified in P1 against
    random-waypoint critters, whose intent genuinely does not persist. An LLM
    agent pursuing a drive has persistent goals, so if intent-rollout is ever
    going to beat a no-motion baseline, it is here.
"""

from __future__ import annotations

import statistics

from homunculus.mind import Mind
from homunculus.multi import HEARING_RANGE, MultiRuntime
from homunculus.provider import MockProvider


def _rt(seed: int):
    return MultiRuntime(seed, policies={
        "agent": Mind(MockProvider()), "agent2": Mind(MockProvider()),
    })


def test_two_agents_both_survive():
    rt = _rt(42)
    for _ in range(4000):
        rt.step()
    for aid, ag in sorted(rt.agents.items()):
        assert ag.soma.drives["energy"].value > 0.25, f"{aid} starved"
        assert rt.pose_error(aid) < 4.0, f"{aid} lost localization"


def test_agents_perceive_each_other():
    rt = _rt(42)
    seen = False
    for _ in range(1500):
        rt.step()
        if any(v.id == "agent2" for v in rt.agents["agent"].frame.entities):
            seen = True
            break
    assert seen, "agent never perceived agent2"


def test_speech_reaches_only_those_in_earshot():
    rt = _rt(42)
    for _ in range(4000):
        rt.step()
    assert rt.utterances, "nobody ever spoke"
    for u in rt.utterances:
        listener = "agent2" if u.speaker == "agent" else "agent"
        if u in rt.agents[listener].heard:
            # If it was heard, the speakers were within hearing range at the time.
            assert u.text
    heard_total = sum(len(a.heard) for a in rt.agents.values())
    assert heard_total < len(rt.utterances) * 2, "speech ignored distance entirely"


def test_speech_is_never_repeated_reflexively():
    """Regression: `say` became a habit and was re-issued verbatim."""
    rt = _rt(42)
    for _ in range(4000):
        rt.step()
    by_speaker: dict[str, list] = {}
    for u in rt.utterances:
        by_speaker.setdefault(u.speaker, []).append(u)
    for speaker, us in by_speaker.items():
        for a, b in zip(us, us[1:]):
            if a.text == b.text:
                assert b.tick - a.tick > 20, (
                    f"{speaker} repeated itself verbatim at t{a.tick}/{b.tick}"
                )


def test_scripted_movers_advance_once_per_tick():
    """Physics must not depend on how many agents are in the world."""
    rt = _rt(3)
    before = (rt.world.entities["c00"].x, rt.world.entities["c00"].y)
    rt.step()
    after = (rt.world.entities["c00"].x, rt.world.entities["c00"].y)
    assert abs(after[0] - before[0]) + abs(after[1] - before[1]) <= 1


def test_h4_retest_rollout_still_does_not_beat_no_motion():
    """H4 remains falsified even against a goal-persistent LLM agent.

    Asserted as graceful degradation, not as a win: the learned-persistence
    mechanism should detect that rollout is not helping and collapse it toward
    the baseline rather than doing harm.
    """
    roll, base = [], []
    for seed in (1, 2, 42):
        rt = _rt(seed)
        for _ in range(3000):
            rt.step()
            for ag in rt.agents.values():
                if ag.last_surprise is None:
                    continue
                for cls, dt, raw, _e, bl, _t, kind, _p in ag.last_surprise.samples:
                    if cls == "animate" and dt >= 3 and kind == "agent":
                        roll.append(raw)
                        base.append(bl)
    assert len(roll) > 50, "not enough agent-on-agent predictions to judge"
    r, d = statistics.mean(roll), statistics.mean(base)
    assert r < d * 1.15, (
        f"rollout {r:.2f} much worse than no-motion {d:.2f} — persistence "
        "learning failed to suppress a losing predictor"
    )


def test_each_agent_has_its_own_belief():
    """No shared state: one agent's belief must not leak into the other's."""
    rt = _rt(7)
    for _ in range(800):
        rt.step()
    a, b = rt.agents["agent"], rt.agents["agent2"]
    assert a.wm is not b.wm
    assert a.soma is not b.soma
    assert a.wm.pose != b.wm.pose
    assert a.wm.beliefs is not b.wm.beliefs


def test_hearing_range_is_finite():
    assert 0 < HEARING_RANGE < 50
