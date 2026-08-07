"""Reconstruct world state at any tick from a recorded event log.

This is the read path, and it is intentionally RNG-free: because tick events
record concrete move outcomes, state at tick N is the pure fold of the initial
snapshot plus every move up to N. That is what "the log can reconstruct any
tick's full state" (the P0 exit test) means. Note this is re-fold-of-a-log, a
different guarantee from seeded re-execution (which lives in loop.py + rng.py).
"""

from __future__ import annotations


class Replay:
    def __init__(self, events: list[dict]):
        self.events = events
        self.start = next(e for e in events if e["type"] == "run_start")
        self.ticks = [e for e in events if e["type"] == "tick"]

    def kinds(self) -> dict[str, str]:
        return {e["id"]: e["kind"] for e in self.start["entities"]}

    def state_at(self, n: int) -> dict[str, tuple[int, int]]:
        pos = {e["id"]: (e["x"], e["y"]) for e in self.start["entities"]}
        for ev in self.ticks:
            if ev["t"] > n:
                break
            for m in ev["moves"]:
                pos[m["id"]] = (m["to"][0], m["to"][1])
        return pos
