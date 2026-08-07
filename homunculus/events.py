"""Canonical event serialization.

Byte-identical replay depends on deterministic JSON: sorted keys, compact
separators, ASCII-only. Positions are integers and there are no timestamps in
the event stream (wall-clock lives in a run sidecar, never here), so two runs
from the same seed serialize to identical bytes.
"""

from __future__ import annotations

import json
from typing import Iterable


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_bytes(events: Iterable[dict]) -> bytes:
    """The exact bytes an EventLog would write — used by the determinism test."""
    return ("".join(dumps(e) + "\n" for e in events)).encode("utf-8")
