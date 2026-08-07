"""Measurements for the hypotheses in PLAN.md.

H1 is the load-bearing one: does surprise-gating cut LLM calls by a large factor
without degrading behaviour? The comparison must be on IDENTICAL seeds and an
identical world, changing only whether the gate is present — otherwise it
measures world variance rather than the gate.

Task score is deliberately body-based (how well homeostasis was held), not a
proxy the gate could game. An agent that thinks less but lives worse has not
validated H1.
"""

from __future__ import annotations

import random
import statistics

from .gate import SurpriseGate
from .loop import Runtime
from .mind import Mind
from .provider import MockProvider


def task_score(stats: dict) -> float:
    """How well the body was kept near its setpoints. 1.0 is perfect."""
    e = statistics.mean(stats["energy"])
    w = statistics.mean(stats["warmth"])
    f = statistics.mean(stats["fatigue"])
    # Distance from setpoint, normalized; fatigue's setpoint is 0.40.
    pen = abs(e - 0.75) + abs(w - 0.70) + abs(f - 0.40)
    return max(0.0, 1.0 - pen / 1.5)


def run_condition(seed: int, ticks: int, condition: str, provider=None,
                  target_calls_per_1k: float = 12.0):
    """Three conditions, because two effects are being separated:

      every_tick  consult the mind on every single tick (the naive baseline —
                  this is the 36,000-calls/hour figure from PLAN.md)
      idle_only   consult only when the current action ends (durative action)
      gated       idle + surprise + heartbeat (the full architecture)

    Reporting only gated-vs-idle would hide how much of the saving durative
    action already provides; reporting only gated-vs-every-tick would credit the
    gate with work the motor is doing.
    """
    prov = provider or MockProvider()
    mind = Mind(prov)
    gate = None
    if condition == "gated":
        gate = SurpriseGate(target_calls_per_1k=target_calls_per_1k)
    elif condition == "every_tick":
        # Interrupts must be explicitly enabled, otherwise this silently
        # degenerates into idle_only and the baseline measures nothing.
        gate = SurpriseGate(threshold=-1.0, heartbeat=1, adapt=False,
                            allow_interrupt=True, habit=False)
    rt = Runtime(seed, policy=mind, gate=gate)

    stats = {"energy": [], "warmth": [], "fatigue": [], "eats": 0}
    for _ in range(ticks):
        ev = rt.step()
        stats["eats"] += 1 if ev.get("consumed") else 0
        for k in ("energy", "warmth", "fatigue"):
            stats[k].append(rt.soma.drives[k].value)

    return {
        "seed": seed,
        "ticks": ticks,
        "condition": condition,
        "calls": mind.calls,
        "errors": mind.errors,
        "calls_per_1k": mind.calls * 1000.0 / ticks,
        "cost_usd": mind.cost,
        "tokens": mind.tokens,
        "cached_tokens": mind.cached_tokens,
        "score": task_score(stats),
        "eats": stats["eats"],
        "gate_reasons": dict(gate.stats.by_reason) if gate else {},
        "threshold": round(gate.threshold, 2) if gate else None,
    }


CONDITIONS = ("every_tick", "idle_only", "gated")

# --------------------------------------------------------------------------
# H2 — does surprise-weighted consolidation beat recency-only and random?
# --------------------------------------------------------------------------


def _collect_episodes(seed: int, ticks: int):
    """Run once and capture the stream of episode writes plus ground truth.

    The same stream is then replayed into every store, so the comparison
    isolates the RETENTION POLICY rather than run-to-run variance.
    """
    from .gate import SurpriseGate
    from .memory import Memory
    from .mind import Mind

    mem = Memory(capacity=10_000, policy="recency")   # unbounded: capture all
    rt = Runtime(seed, policy=Mind(MockProvider()), gate=SurpriseGate(),
                 memory=mem)
    for _ in range(ticks):
        rt.step()
    writes = [(e.tick, e.text, e.surprise, e.entities) for e in mem.episodic.items]
    return writes, rt.tick


def h2(seeds=(1, 2, 3, 11), ticks=6000, capacity=60, k=4):
    """Recall@k for notable events under three retention policies."""
    from .memory import EpisodicStore

    results = {p: [] for p in EpisodicStore.POLICIES}
    for seed in seeds:
        writes, end = _collect_episodes(seed, ticks)
        if len(writes) < capacity * 2:
            continue
        # Probes must be chosen on a criterion INDEPENDENT of the score under
        # test. Ranking them by surprise would be circular: the surprise policy
        # retains exactly the items the probe then asks about. So probe on
        # objective world-state changes — food consumed, entities appearing or
        # vanishing — sampled uniformly across the run.
        notable = [w for w in writes if "ate " in w[1] or w[3]]
        if len(notable) < 8:
            continue
        rng = random.Random(seed)
        probes = rng.sample(notable, min(len(notable), max(12, capacity // 3)))

        for policy in EpisodicStore.POLICIES:
            store = EpisodicStore(capacity=capacity, policy=policy, seed=seed)
            for tick, text, surp, ents in writes:
                store.add(tick, text, surp, ents)
            hits = 0
            for tick, text, _surp, ents in probes:
                query = " ".join(ents) if ents else text
                got = store.retrieve(query, end, k=k)
                if any(abs(g.tick - tick) <= 2 for g in got):
                    hits += 1
            results[policy].append(hits / max(len(probes), 1))
    return {p: (statistics.mean(v) if v else 0.0) for p, v in results.items()}


def h3(seeds=(1, 2, 3, 11), ticks=6000):
    """H3 — does the agent act to reduce uncertainty?

    An epistemic action is one whose ONLY payoff is variance reduction: going to
    look at something it is no longer confident about, while no drive is urgent.
    Counting intentions is not enough — the belief must actually be refreshed —
    so this also checks that confidence rose afterwards.
    """
    from .gate import SurpriseGate
    from .mind import Mind

    episodes, refreshed = 0, 0
    for seed in seeds:
        rt = Runtime(seed, policy=Mind(MockProvider()), gate=SurpriseGate())
        pending: dict[str, float] = {}
        for _ in range(ticks):
            ev = rt.step()
            dec = ev.get("decision")
            if dec and dec.get("verb") == "goto":
                tgt = dec.get("target")
                view = next((v for v in rt.frame.entities if v.id == tgt), None)
                urgent = (
                    rt.soma.drives["energy"].value < 0.55
                    or rt.soma.drives["warmth"].value < 0.50
                )
                # Only counts as epistemic if nothing was pressing and the
                # target was genuinely uncertain.
                if view is not None and not urgent and view.conf < 0.6:
                    episodes += 1
                    pending[tgt] = view.conf
            for tgt, before in list(pending.items()):
                v = next((x for x in rt.frame.entities if x.id == tgt), None)
                if v is not None and v.observed:
                    if v.conf > before:
                        refreshed += 1
                    del pending[tgt]
    return {
        "epistemic_actions": episodes,
        "beliefs_refreshed": refreshed,
        "refresh_rate": refreshed / max(episodes, 1),
    }


def format_h3(res: dict) -> str:
    return (
        "H3: does the agent move to reduce uncertainty?\n\n"
        f"  epistemic actions taken : {res['epistemic_actions']}\n"
        f"  beliefs actually refreshed: {res['beliefs_refreshed']}\n"
        f"  refresh rate            : {res['refresh_rate']:.2f}"
    )


def format_h2(res: dict) -> str:
    out = ["H2: which retention policy preserves answerable memories?", ""]
    for p, v in sorted(res.items(), key=lambda kv: -kv[1]):
        out.append(f"  {p:>9}  recall@k = {v:.3f}")
    best = max(res, key=res.get)
    out.append("")
    out.append(f"  best: {best}")
    return "\n".join(out)


def h1(seeds=(1, 2, 3), ticks=5000, provider_factory=MockProvider):
    """All three conditions on identical seeds. Returns (rows, summary)."""
    rows = []
    for s in seeds:
        for cond in CONDITIONS:
            rows.append(run_condition(s, ticks, cond, provider_factory()))

    def agg(cond, key):
        vals = [r[key] for r in rows if r["condition"] == cond]
        return statistics.mean(vals) if vals else 0.0

    base = agg("every_tick", "calls")
    summary = {c: {k: agg(c, k) for k in ("calls", "score", "cost_usd", "eats")}
               for c in CONDITIONS}
    for c in CONDITIONS:
        summary[c]["reduction_x"] = base / max(summary[c]["calls"], 1e-9)
        summary[c]["score_delta_pct"] = (
            100.0 * (summary[c]["score"] - summary["every_tick"]["score"])
            / max(summary["every_tick"]["score"], 1e-9)
        )
    return rows, summary


def format_h1(rows, summary) -> str:
    out = ["H1: does surprise-gating cut cognition without degrading behaviour?", ""]
    out.append(f"{'seed':>5} {'condition':>11} {'calls':>7} {'/1k':>7} "
               f"{'score':>6} {'eats':>5} {'cost$':>9}  gate reasons")
    order = {c: i for i, c in enumerate(CONDITIONS)}
    for r in sorted(rows, key=lambda r: (r["seed"], order[r["condition"]])):
        out.append(
            f"{r['seed']:>5} {r['condition']:>11} {r['calls']:>7} "
            f"{r['calls_per_1k']:>7.1f} {r['score']:>6.3f} {r['eats']:>5} "
            f"{r['cost_usd']:>9.4f}  {r['gate_reasons'] or ''}"
        )
    out.append("")
    out.append(f"{'condition':>11} {'calls':>8} {'vs every-tick':>14} "
               f"{'score':>7} {'delta':>8} {'cost$':>9}")
    for c in CONDITIONS:
        s = summary[c]
        out.append(
            f"{c:>11} {s['calls']:>8.0f} {s['reduction_x']:>13.1f}x "
            f"{s['score']:>7.3f} {s['score_delta_pct']:>+7.1f}% {s['cost_usd']:>9.4f}"
        )
    return "\n".join(out)
