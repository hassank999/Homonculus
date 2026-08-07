"""Seeded randomness with named sub-streams.

Determinism is a P0 exit requirement, so every source of randomness in the sim
flows through here. Two rules make it reproducible across processes:

  1. Sub-stream seeds are derived with hashlib, NOT the builtin hash(), which is
     salted per-process by PYTHONHASHSEED and would make runs non-reproducible.
  2. Each named stream is an independent random.Random, so consumers (world,
     agent, ...) can't perturb each other's sequence by call-ordering accidents.
"""

from __future__ import annotations

import hashlib
import random


class HRng:
    """A seed plus a lazily-created set of named, independent RNG streams."""

    def __init__(self, seed: int):
        self.seed = int(seed)
        self._streams: dict[str, random.Random] = {}

    def stream(self, name: str) -> random.Random:
        s = self._streams.get(name)
        if s is None:
            digest = hashlib.sha256(f"{self.seed}:{name}".encode("utf-8")).digest()
            s = random.Random(int.from_bytes(digest[:8], "big"))
            self._streams[name] = s
        return s
