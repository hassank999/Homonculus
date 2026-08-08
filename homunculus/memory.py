"""Memory: four stores, and the sleep pass that moves things between them.

Context is not memory (PLAN.md §4.2). A single lossy log conflates four
different things, so they are kept apart:

  working     the last N ticks verbatim — what just happened
  episodic    specific events, retrievable by relevance x recency x surprise
  semantic    facts distilled from repeated episodes — what is generally true
  procedural  action patterns that worked — what to do

The consolidation criterion is the surprise signal, reused: what gets kept is
what the agent failed to predict, because that is what its model of the world
did not already contain. Recency-only retention keeps whatever happened last,
which in a repetitive world is mostly nothing.

Embeddings are deliberately a cheap deterministic hash of tokens. A real
embedding model is a drop-in replacement (`Embedder`), but keeping it local
means memory can be tested offline, reproducibly, with no spend.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field

DIM = 64


class Embedder:
    """Deterministic bag-of-tokens hashing. Swappable for a real model."""

    def __init__(self, dim: int = DIM):
        self.dim = dim

    def __call__(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _tokens(text):
            h = hashlib.sha256(tok.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            sign = 1.0 if h[4] % 2 else -1.0
            vec[idx] += sign
        n = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / n for v in vec]


def _tokens(text: str) -> list[str]:
    out, cur = [], []
    for ch in text.lower():
        if ch.isalnum() or ch == "_":
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class Episode:
    tick: int
    text: str
    surprise: float
    entities: tuple = ()
    vec: list = field(default_factory=list)
    retrievals: int = 0
    # When this memory was lost, if it was. Kept so the viewer can show what the
    # agent remembered AT A GIVEN MOMENT, including what it has since forgotten
    # — a store that only reports its final contents hides the forgetting, which
    # is the interesting half of a bounded memory.
    evicted_at: int | None = None
    last_recalled: int | None = None

    def to_dict(self) -> dict:
        return {
            "tick": self.tick, "text": self.text,
            "surprise": round(self.surprise, 3),
            "entities": list(self.entities),
        }


@dataclass
class Fact:
    text: str
    support: int = 1
    last_tick: int = 0
    first_tick: int = 0


class EpisodicStore:
    """Bounded store. What survives eviction is the whole experiment (H2)."""

    POLICIES = ("surprise", "recency", "random")

    def __init__(self, capacity: int = 120, policy: str = "surprise",
                 embedder: Embedder | None = None, seed: int = 0):
        if policy not in self.POLICIES:
            raise ValueError(f"unknown policy {policy!r}")
        self.capacity = capacity
        self.policy = policy
        self.embed = embedder or Embedder()
        self.items: list[Episode] = []
        self.history: list[Episode] = []      # every episode ever stored
        self._rng = random.Random(seed)
        self._now = 0

    def add(self, tick: int, text: str, surprise: float, entities=()) -> Episode:
        self._now = tick
        ep = Episode(tick, text, surprise, tuple(entities), self.embed(text))
        self.items.append(ep)
        self.history.append(ep)
        if len(self.items) > self.capacity:
            self._evict()
        return ep

    def _mark_evicted(self, kept: list[Episode]) -> None:
        keep = {id(e) for e in kept}
        for e in self.items:
            if id(e) not in keep and e.evicted_at is None:
                e.evicted_at = self._now

    def _evict(self) -> None:
        over = len(self.items) - self.capacity
        if over <= 0:
            return
        if self.policy == "recency":
            kept = self.items[over:]
            self._mark_evicted(kept)
            self.items = kept
            return
        if self.policy == "random":
            for _ in range(over):
                gone = self.items.pop(self._rng.randrange(len(self.items)))
                if gone.evicted_at is None:
                    gone.evicted_at = self._now
            return
        # Keep what was hardest to predict. Ties break toward recent so a
        # long-settled world does not freeze its store permanently.
        self.items.sort(key=lambda e: (e.surprise, e.tick))
        kept = self.items[over:]
        self._mark_evicted(kept)
        self.items = sorted(kept, key=lambda e: e.tick)

    def retrieve(self, query: str, tick: int, k: int = 4,
                 half_life: float = 3000.0) -> list[Episode]:
        """Relevance x recency x surprise — the three things that make a memory
        worth surfacing. Retrieval also marks the episode as used."""
        if not self.items:
            return []
        q = self.embed(query)
        scored = []
        for ep in self.items:
            sim = cosine(q, ep.vec)
            recency = 0.5 ** ((tick - ep.tick) / half_life)
            weight = 1.0 + min(ep.surprise, 10.0) / 10.0
            scored.append((sim * recency * weight, ep))
        scored.sort(key=lambda kv: -kv[0])
        top = [ep for _s, ep in scored[:k]]
        for ep in top:
            ep.retrievals += 1
            ep.last_recalled = tick
        return top

    def covers(self, tick: int, tol: int = 0) -> bool:
        return any(abs(e.tick - tick) <= tol for e in self.items)


class SemanticStore:
    """Facts distilled from repeated episodes. Written only by the sleep pass."""

    def __init__(self):
        self.facts: dict[str, Fact] = {}

    def assert_fact(self, text: str, tick: int) -> None:
        f = self.facts.get(text)
        if f is None:
            self.facts[text] = Fact(text, 1, tick, tick)
        else:
            f.support += 1
            f.last_tick = tick

    def top(self, n: int = 8) -> list[Fact]:
        return sorted(self.facts.values(), key=lambda f: (-f.support, f.text))[:n]


class ProceduralStore:
    """What actually worked. Keyed by (situation, action)."""

    def __init__(self):
        self.stats: dict[tuple, list[int]] = {}

    def record(self, situation: str, verb: str, target: str | None,
               success: bool) -> None:
        key = (situation, verb, target)
        s = self.stats.setdefault(key, [0, 0])
        s[0] += 1
        s[1] += 1 if success else 0

    def best(self, situation: str):
        cands = [
            (wins / max(n, 1), n, key)
            for key, (n, wins) in self.stats.items()
            if key[0] == situation and n >= 3
        ]
        if not cands:
            return None
        cands.sort(key=lambda c: (-c[0], -c[1]))
        return cands[0][2]


class Memory:
    """The four stores plus consolidation."""

    def __init__(self, capacity: int = 120, policy: str = "surprise",
                 working: int = 30, seed: int = 0):
        self.working: list[dict] = []
        self.working_size = working
        self.episodic = EpisodicStore(capacity, policy, seed=seed)
        self.semantic = SemanticStore()
        self.procedural = ProceduralStore()
        self.sleeps = 0

    def observe(self, tick: int, frame, surprise, event: dict) -> None:
        rec = {
            "tick": tick,
            "surprise": round(surprise.scalar, 3),
            "valence": round(frame.valence, 3),
            "action": event.get("action"),
            "existence": list(surprise.existence),
            "consumed": event.get("consumed"),
            "blocked": bool(event.get("blocked")),
        }
        self.working.append(rec)
        if len(self.working) > self.working_size:
            self.working.pop(0)

        # Write-through to episodic only for things worth remembering. A tick
        # that went exactly as predicted taught the agent nothing.
        notable = (
            surprise.scalar >= 2.0
            or event.get("consumed")
            or surprise.existence
        )
        if notable:
            # Tag the episode with what it was ABOUT: anything whose existence
            # changed, plus what was actually in view. Tagging only existence
            # changes left most episodes with no subject at all, so
            # consolidation had nothing to count and produced zero facts.
            subjects = {t[1:] for t in surprise.existence if t[:1] in "+-~"}
            subjects.update(e.id for e in frame.entities if e.observed)
            if event.get("consumed"):
                subjects.add(event["consumed"])
            self.episodic.add(
                tick, self._describe(tick, frame, surprise, event),
                surprise.scalar, entities=tuple(sorted(subjects)),
            )

    @staticmethod
    def _describe(tick: int, frame, surprise, event: dict) -> str:
        """A short human-readable account of the episode.

        Deliberately excludes the tick and the nearby-entity list: both are
        carried as structured fields, and repeating them here made the stored
        memories read as noise in the viewer.
        """
        bits = []
        if event.get("consumed"):
            bits.append(f"ate {event['consumed']}")
        for tag in surprise.existence:
            what = tag[1:]
            how = {"+": "appeared", "-": "was missing", "~": "changed"}.get(tag[0])
            if how:
                bits.append(f"{what} {how}")
        if event.get("blocked"):
            bits.append("bumped into something")
        if not bits:
            act = event.get("action") or {}
            verb = act.get("verb") or "acted"
            near = sorted(e.id for e in frame.entities if e.observed)[:3]
            bits.append(
                f"{verb} near {', '.join(near)}" if near else f"{verb} alone"
            )
        return "; ".join(bits)

    def sleep(self, tick: int) -> dict:
        """Offline consolidation: distil repeated episodes into facts.

        Run away from the critical path (PLAN.md §6 — Together's batch discount
        does not cover modern models, so this is an ordinary async pass rather
        than a batch job). Nothing here needs the world to be running.
        """
        self.sleeps += 1
        counts: dict[str, int] = {}
        for ep in self.episodic.items:
            for ent in ep.entities:
                counts[ent] = counts.get(ent, 0) + 1
        made = 0
        total = max(len(self.episodic.items), 1)
        for ent, n in sorted(counts.items()):
            # A fact needs repetition AND prominence: something present in a
            # decent share of what was worth remembering.
            if n >= 3 and n / total >= 0.25:
                self.semantic.assert_fact(f"{ent} figures in what surprises me", tick)
                made += 1
        blocked = sum(1 for r in self.working if r.get("blocked"))
        if blocked > self.working_size // 3:
            self.semantic.assert_fact("this route is frequently blocked", tick)
            made += 1
        return {"facts": made, "episodes": len(self.episodic.items)}

    def recall(self, query: str, tick: int, k: int = 3) -> list[Episode]:
        return self.episodic.retrieve(query, tick, k)

    def export(self) -> dict:
        """Everything needed to reconstruct memory AS IT WAS at any tick.

        Episodes carry both when they were stored and when they were forgotten,
        so a viewer can show the store's contents at a moment rather than only
        its final state — the forgetting is half of what a bounded memory does.
        """
        return {
            "capacity": self.episodic.capacity,
            "policy": self.episodic.policy,
            "episodic": [
                {
                    "t": e.tick,
                    "text": e.text,
                    "s": round(e.surprise, 2),
                    "ents": list(e.entities)[:6],
                    "gone": e.evicted_at,
                    "recalls": e.retrievals,
                    "lastRecall": e.last_recalled,
                }
                for e in self.episodic.history
            ],
            "semantic": [
                {"text": f.text, "support": f.support,
                 "first": f.first_tick, "last": f.last_tick}
                for f in sorted(self.semantic.facts.values(),
                                key=lambda f: (f.first_tick, f.text))
            ],
            "procedural": [
                {"situation": k[0], "verb": k[1], "target": k[2],
                 "n": v[0], "wins": v[1]}
                # Coerce the optional target for sorting: a real model may
                # attach a target to a verb that does not need one, which mixes
                # None and str in otherwise-identical keys and makes the
                # comparison explode. The mock never did this.
                for k, v in sorted(self.procedural.stats.items(),
                                   key=lambda kv: (kv[0][0], kv[0][1],
                                                   kv[0][2] or ""))
            ],
        }
