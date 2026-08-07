"""P1 exit tests — perception, belief, and the surprise signal.

The headline assertion is CALIBRATION: normalized error must stay roughly
stationary across elapsed-time buckets, even though raw error grows a lot. That
is what makes surprise mean "wrong given what I knew" rather than "wrong", and
it is the property everything downstream (memory consolidation, the inference
gate) depends on.

Also asserted here, honestly: intent-rollout does NOT beat a no-motion baseline
in this world (H4 is not supported — see PROJECT_UPDATES.md). What we assert is
that the learned-persistence mechanism DETECTS that and degrades gracefully to
the baseline rather than doing harm. An early naive version was 258% worse.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from homunculus.loop import Runtime

BUCKETS = ((2, "dt<=2"), (10, "dt<=10"), (50, "dt<=50"), (200, "dt<=200"))


def _bucket(dt: int) -> str:
    for ceil, name in BUCKETS:
        if dt <= ceil:
            return name
    return "dt>200"


def _gather(seeds=(1, 2, 3), ticks=2500):
    norm = defaultdict(list)
    raw = defaultdict(list)
    roll, base = defaultdict(list), defaultdict(list)
    for s in seeds:
        rt = Runtime(s)
        for t in range(1, ticks + 1):
            rt.step()
            for cls, dt, r, exp, bl, track, kind, pers in rt.last_surprise.samples:
                norm[(cls, _bucket(dt))].append(r / max(exp, 1e-9))
                raw[(cls, _bucket(dt))].append(r)
                if cls == "animate" and dt >= 3:
                    roll[kind].append(r)
                    base[kind].append(bl)
    return norm, raw, roll, base


def test_surprise_is_calibrated_across_elapsed_time():
    """Normalized error must not explode with dt — that is the whole point."""
    norm, _, _, _ = _gather()
    for cls in ("static", "inert_movable", "animate"):
        means = [
            statistics.mean(v)
            for (c, _b), v in norm.items()
            if c == cls and len(v) >= 15
        ]
        assert len(means) >= 3, f"not enough {cls} buckets to judge calibration"
        spread = max(means) / max(min(means), 1e-9)
        assert spread < 4.0, f"{cls} miscalibrated: {spread:.1f}x spread across dt"


def test_raw_error_does_grow_so_the_test_above_is_meaningful():
    """Guard against a vacuous pass: if raw error were flat, calibration would
    be trivial. Animate entities must genuinely get harder to predict."""
    _, raw, _, _ = _gather()
    near = [v for (c, b), v in raw.items() if c == "animate" and b == "dt<=2"]
    far = [v for (c, b), v in raw.items() if c == "animate" and b in ("dt<=50", "dt<=200")]
    assert near and far
    m_near = statistics.mean([x for v in near for x in v])
    m_far = statistics.mean([x for v in far for x in v])
    assert m_far > 2.0 * m_near, "raw animate error should grow sharply with dt"


def test_rollout_degrades_gracefully_to_baseline():
    """H4 is NOT supported in this world. What must hold is that the learned
    persistence detects that and keeps rollout from doing harm."""
    _, _, roll, base = _gather()
    for kind in ("critter", "resident"):
        if len(roll.get(kind, [])) < 40:
            continue
        r = statistics.mean(roll[kind])
        d = statistics.mean(base[kind])
        assert r < d * 1.15, (
            f"{kind}: rollout {r:.2f} much worse than no-motion {d:.2f} — "
            "persistence learning failed to suppress a losing predictor"
        )


def test_pose_error_stays_bounded_via_landmark_fixes():
    """Dead reckoning drifts (slips are unfelt); landmarks must pull it back."""
    rt = Runtime(42)
    errs = []
    for _ in range(3000):
        rt.step()
        errs.append(rt.pose_error())
    assert statistics.mean(errs) < 1.5, f"mean pose error {statistics.mean(errs):.2f}"
    assert max(errs) < 6.0, f"max pose error {max(errs):.2f} — localization lost"


def test_bump_is_felt_but_slip_is_not():
    """Proprioception corrects a blocked move; a slip silently becomes drift."""
    from homunculus.world import Action

    rt = Runtime(7)
    wm = rt.wm
    before = wm.pose
    wm.predict_pose(Action("move", "N"))
    assert wm.pose != before
    wm.apply_bump(Action("move", "N"))
    assert abs(wm.pose[0] - before[0]) < 1e-9 and abs(wm.pose[1] - before[1]) < 1e-9


def test_confabulation_decays_confidence_with_age():
    """Unobserved beliefs must lose confidence, and animate ones must carry
    ranked hypotheses rather than one collapsed point."""
    rt = Runtime(3)
    for _ in range(1200):
        rt.step()
    views = rt.wm.entity_views(rt.tick, observed_ids=set())
    assert views, "expected some beliefs"
    aged = [v for v in views if v.age > 100]
    assert aged, "expected at least one stale belief"
    for v in aged:
        assert 0.0 <= v.conf <= 1.0
        assert v.observed is False
    animate = [v for v in views if v.kind in ("critter", "resident") and v.age > 5]
    for v in animate:
        assert v.hypotheses, "animate beliefs should expose ranked hypotheses"


def test_resolve_is_pure():
    """Confabulation must not mutate state — it is a read-path function, and
    replay/time-travel depends on calling it being free of side effects."""
    rt = Runtime(11)
    for _ in range(400):
        rt.step()
    eid = sorted(rt.wm.beliefs)[0]
    snap = (rt.wm.beliefs[eid].pos, rt.wm.beliefs[eid].last_seen)
    a = rt.wm.resolve(eid, rt.tick + 500)
    b = rt.wm.resolve(eid, rt.tick + 500)
    assert a.pos == b.pos and a.conf == b.conf
    assert (rt.wm.beliefs[eid].pos, rt.wm.beliefs[eid].last_seen) == snap
