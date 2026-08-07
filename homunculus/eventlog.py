"""Append-only JSONL event log.

Minimal and ours for P0 — deliberately not yet Vivarium's crash-tolerant writer
(PLAN.md §10 lifts that later, when fsync policy and partial-tail recovery earn
their keep). The one thing this MUST get right now is byte-identity: newline="\\n"
forces LF so Windows CRLF translation can't corrupt the determinism guarantee.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .events import dumps


class EventLog:
    def __init__(self, path):
        self.path = Path(path)

    def write(self, events: Iterable[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8", newline="\n") as f:
            for e in events:
                f.write(dumps(e))
                f.write("\n")

    def read(self) -> list[dict]:
        out: list[dict] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
