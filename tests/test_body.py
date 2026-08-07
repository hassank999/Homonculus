"""P2 exit tests — drives ground behaviour, and action is durative.

Two claims:

  * Drives make things MATTER. A policy reading nothing but drive error should
    keep the body near its setpoints — eat when hungry, warm up when cold, rest
    when tired — with no language model anywhere.
  * Action persists across ticks. Deliberation must be RARE, because that is
    what makes surprise-gated cognition affordable in P3. If the agent had to
    decide every tick, the gate could never pay for itself.

Several tests here are regressions for bugs that cost real debugging time; each
names what it is guarding so the failure mode does not silently return.
"""

from __future__ import annotations

import statistics

from homunculus.loop import Runtime
from homunculus.motor import DONE, FAILED, RUNNING
from homunculus.soma import Soma
from homunculus.world import Action

SEEDS = (42, 7, 3)
TICKS = 6000


def _run(seed: int, ticks: int = TICKS):
    rt = Runtime(seed)
    stats = {"eats": 0, "bumps": 0, "energy": [], "warmth": [], "fatigue": []}
    for _ in range(ticks):
        ev = rt.step()
        stats["eats"] += 1 if ev.get("consumed") else 0
        stats["bumps"] += 1 if ev.get("blocked") else 0
        for k in ("energy", "warmth", "fatigue"):
            stats[k].append(rt.soma.drives[k].value)
    return rt, stats


def test_homeostasis_is_maintained():
    """The headline P2 claim: drives alone produce self-sustaining behaviour."""
    for seed in SEEDS:
        rt, s = _run(seed)
        e, w = statistics.mean(s["energy"]), statistics.mean(s["warmth"])
        assert e > 0.45, f"seed {seed}: starved, mean energy {e:.2f}"
        assert w > 0.45, f"seed {seed}: froze, mean warmth {w:.2f}"
        assert s["eats"] >= 3, f"seed {seed}: only ate {s['eats']} times"


def test_deliberation_is_rare_because_action_is_durative():
    """Ticks-per-decision is the number that makes P3's gate viable."""
    for seed in SEEDS:
        rt, _ = _run(seed)
        per = TICKS / max(rt.decisions, 1)
        assert per > 20, f"seed {seed}: deliberating every {per:.0f} ticks"


def test_commitments_complete_in_a_real_run():
    rt = Runtime(42)
    seen = set()
    prev = None
    for _ in range(4000):
        rt.step()
        c = rt.motor.current
        if c is not None and c is not prev and c.status != RUNNING:
            seen.add(c.status)
            prev = c
    assert DONE in seen, "no commitment ever completed"


def test_commitments_can_fail():
    """Failure is provoked deterministically rather than hoped for.

    A competent agent fails rarely — once it stopped chasing things it could
    never catch, natural failures became scarce — so waiting for one in a live
    run made this a test of how badly the policy behaves."""
    rt = Runtime(42)
    for _ in range(50):
        rt.step()
    c = rt.motor.start("goto", "no_such_entity")
    assert c.status == FAILED
    assert c.reason == "no_path"


def test_commitment_is_interruptible():
    """A `wait` is used because it is unconditionally RUNNING; a `goto` can
    legitimately resolve on the spot (already there / no route), which would
    make this a test of pathfinding rather than of interruption."""
    rt = Runtime(5)
    for _ in range(50):
        rt.step()
    c = rt.motor.start("wait")
    c.meta["duration"] = 100
    rt.motor.step()
    assert rt.motor.busy(), "wait should still be running"
    rt.motor.interrupt("test")
    assert not rt.motor.busy()
    assert rt.motor.current.status == FAILED
    assert rt.motor.current.reason == "test"


def test_failed_eat_reports_failure():
    """Regression: `eat` used to report DONE unconditionally, so the agent
    'ate' 225 times while starving beside food it was never standing on."""
    rt = Runtime(42)
    for _ in range(30):
        rt.step()
    rt.motor.start("eat", "food_a")
    rt.motor.step()
    rt.motor.note_result(blocked=False, consumed=None)
    assert rt.motor.current.status == FAILED
    assert rt.motor.current.reason == "nothing_here"

    rt.motor.start("eat", "food_a")
    rt.motor.step()
    rt.motor.note_result(blocked=False, consumed="food_a")
    assert rt.motor.current.status == DONE


def test_collision_memory_prevents_livelock():
    """Regression: the agent once bumped the same wall 715 times in one run
    because emitting a move reset the stuck counter. Collisions must be
    remembered so replanning routes around them."""
    for seed in SEEDS:
        _, s = _run(seed, ticks=4000)
        assert s["bumps"] < 400, f"seed {seed}: {s['bumps']} collisions — livelock"


def test_drives_respond_to_the_world():
    soma = Soma()
    e0 = soma.drives["energy"].value
    soma.step(near_food_eaten=True, near_warmth=False, moved=False)
    assert soma.drives["energy"].value > e0, "eating should restore energy"

    w0 = soma.drives["warmth"].value
    soma.step(near_food_eaten=False, near_warmth=True, moved=False)
    assert soma.drives["warmth"].value > w0, "a heat source should warm you"

    soma.drives["fatigue"].value = 0.8
    soma.step(near_food_eaten=False, near_warmth=False, moved=False)
    assert soma.drives["fatigue"].value < 0.8, "resting should reduce fatigue"


def test_affect_is_computed_not_injected():
    """Valence must track drive error rather than being set by hand."""
    soma = Soma()
    for d in soma.drives.values():
        d.value = d.setpoint
    good = soma.valence
    soma.drives["energy"].value = 0.0
    bad = soma.valence
    assert bad < good, "starving should feel worse than satisfied"


def test_bump_localization_recovers_pose():
    """A collision constrains where the agent can be, given the known map."""
    rt = Runtime(42)
    for _ in range(200):
        rt.step()
    rt.wm.pose = (rt.wm.pose[0] + 2.0, rt.wm.pose[1], rt.wm.pose[2])
    before = rt.pose_error()
    for _ in range(600):
        rt.step()
    assert rt.pose_error() < max(before, 3.0), "never recovered from injected error"
