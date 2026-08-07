"""CLI: python -m homunculus run --seed 42 --ticks 5000 --out runs/latest/events.jsonl"""

from __future__ import annotations

import argparse

from .eventlog import EventLog
from .loop import run


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="homunculus")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the sim and write an event log")
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--ticks", type=int, default=1000)
    r.add_argument("--scenario", default="apartment")
    r.add_argument("--out", default="runs/latest/events.jsonl")

    args = p.parse_args(argv)

    if args.cmd == "run":
        events, _ = run(args.seed, args.ticks, args.scenario)
        EventLog(args.out).write(events)
        print(
            f"wrote {len(events)} events to {args.out} "
            f"(seed={args.seed}, ticks={args.ticks}, scenario={args.scenario})"
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
