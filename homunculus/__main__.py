"""CLI.

    python -m homunculus run --ticks 5000 --mind mock --view
    python -m homunculus experiment h1
    python -m homunculus experiment all
"""

from __future__ import annotations

import argparse

from .eventlog import EventLog
from .gate import Governor, SurpriseGate
from .loop import Runtime, _run_start_event
from .memory import Memory
from .mind import Mind
from .provider import MODELS, build as build_provider


def _run(args) -> int:
    provider = None
    policy = None
    if args.mind != "none":
        provider = build_provider(
            args.mind, model=MODELS.get(args.model, args.model)
        )
        policy = Mind(provider)

    gate = None if args.no_gate else SurpriseGate(
        target_calls_per_1k=args.target_calls
    )
    governor = Governor(min_interval_s=args.min_interval) if args.min_interval else None
    memory = Memory(capacity=args.memory) if args.memory else None

    rt = Runtime(args.seed, args.scenario, policy=policy, gate=gate,
                 governor=governor, memory=memory, sleep_every=args.sleep_every)

    events = [_run_start_event(args.seed, args.scenario, rt.world, args.ticks,
                               getattr(rt.policy, "name", "policy"))]
    for _ in range(args.ticks):
        events.append(rt.step())
    events.append({"type": "run_end", "t": args.ticks, "ticks": args.ticks})

    EventLog(args.out).write(events)
    print(f"wrote {len(events)} events to {args.out}")

    d = rt.soma.to_dict()
    print(f"  drives     energy={d['energy']:.2f} warmth={d['warmth']:.2f} "
          f"fatigue={d['fatigue']:.2f}")
    print(f"  cognition  decisions={rt.decisions} habits={rt.habits} "
          f"({args.ticks / max(rt.decisions, 1):.0f} ticks/decision)")
    if isinstance(rt.policy, Mind):
        s = rt.policy.stats()
        print(f"  mind       calls={s['calls']} errors={s['errors']} "
              f"cost=${s['cost_usd']:.4f} model={s['model']}")
        if s["calls"]:
            # Cache hit rate is the difference between the modelled cost and
            # roughly 4x it, so it is worth seeing on every run.
            prompt = max(s["tokens"] - 0, 1)
            print(f"  tokens     total={s['tokens']} cached={s['cached_tokens']} "
                  f"({100.0 * s['cached_tokens'] / prompt:.0f}% of all tokens) "
                  f"· ${s['cost_usd'] / s['calls']:.5f}/call")
    if memory is not None:
        print(f"  memory     episodic={len(memory.episodic.items)} "
              f"facts={len(memory.semantic.facts)} sleeps={memory.sleeps}")

    if args.view:
        from .viewer import build as build_view
        p = build_view(events, args.view,
                       stride=max(1, args.ticks // 1500),
                       model=provider.model if provider else None,
                       memory=memory)
        print(f"  viewer     {p}")

    if args.stream:
        from .narrate import stream, to_text
        entries = stream(events)
        print(f"\n--- conscious stream ({len(entries)} entries) ---")
        print(to_text(entries, limit=args.stream))
    return 0


def _experiment(args) -> int:
    from . import experiments as X

    which = args.which
    if which in ("h1", "all"):
        rows, s = X.h1(seeds=tuple(args.seeds), ticks=args.ticks)
        print(X.format_h1(rows, s), "\n")
    if which in ("h2", "all"):
        print(X.format_h2(X.h2(seeds=tuple(args.seeds), ticks=args.ticks)), "\n")
    if which in ("h3", "all"):
        print(X.format_h3(X.h3(seeds=tuple(args.seeds), ticks=args.ticks)), "\n")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="homunculus")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the sim and write an event log")
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--ticks", type=int, default=2000)
    r.add_argument("--scenario", default="apartment")
    r.add_argument("--out", default="runs/latest/events.jsonl")
    r.add_argument("--mind", default="mock", choices=("mock", "together", "none"),
                   help="'none' uses the hand-coded reactive policy")
    r.add_argument("--model", default="primary",
                   help="a key from provider.MODELS, or a literal model id")
    r.add_argument("--no-gate", action="store_true")
    r.add_argument("--target-calls", type=float, default=12.0)
    r.add_argument("--memory", type=int, default=200, help="episodic capacity; 0 to disable")
    r.add_argument("--sleep-every", type=int, default=1000)
    r.add_argument("--min-interval", type=float, default=0.0,
                   help="seconds between LLM calls (rate governor)")
    r.add_argument("--view", nargs="?", const="runs/latest/replay.html", default=None)
    r.add_argument("--stream", nargs="?", type=int, const=60, default=None,
                   help="print the conscious stream to the terminal (N entries)")
    r.set_defaults(fn=_run)

    e = sub.add_parser("experiment", help="run a hypothesis measurement")
    e.add_argument("which", choices=("h1", "h2", "h3", "all"))
    e.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    e.add_argument("--ticks", type=int, default=5000)
    e.set_defaults(fn=_experiment)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
