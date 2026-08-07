"""The homunculus: the LLM in the loop.

Prompt assembly obeys one rule above all — STRICT STABILITY ORDER:

    [ world rules, persona, action grammar, drive semantics ]  <- never varies
    [ current Frame ]                                          <- varies each call

Together bills the longest matching prefix at the cached rate (~85-90% off), so
the fixed half must be byte-identical every call. That means no tick counters,
no timestamps, no UUIDs above the boundary, and deterministic serialization
everywhere — the first divergent byte ends the cached span.

The mind emits a structured object; a parse failure is an error path, never a
shrug, because an unparseable tick is a dead agent.
"""

from __future__ import annotations

import json

# The action grammar is closed: the mind may only choose from affordances the
# perception layer generated. This is what keeps an otherwise unbounded
# (verb, noun) space small and legal.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action"],
    "properties": {
        "action": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verb"],
            "properties": {
                "verb": {"type": "string", "enum": ["goto", "eat", "wait"]},
                "target": {"type": "string"},
                "duration": {"type": "integer"},
            },
        },
        "prediction": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "expect_surprise": {
                    "type": "string", "enum": ["low", "medium", "high"],
                },
                "expect": {"type": "string"},
            },
        },
        "note": {"type": "string"},
    },
}

SYSTEM = """You are the deliberative mind of a small embodied creature living in an apartment.

You do not perceive the world directly. Each time you are consulted you receive a
FRAME: your own best belief about your situation, already filtered and possibly
wrong. Your job is to choose ONE action.

HOW TO READ A FRAME
- pose: your believed position and heading. pose_conf near 0 means you are lost.
- drives: your body. Values run 0..1.
    energy   - falls over time, restored by eating. Low energy is hunger.
    warmth   - falls in the open, restored by standing on a heat source.
    fatigue  - rises as you move, falls when you wait. High fatigue is tiredness.
- valence: how well things are going (-1 bad .. +1 good). arousal: how urgent.
- entities: what you believe is around you, in egocentric polar form.
    bearing  - degrees relative to your heading; 0 is straight ahead.
    range    - distance in cells.
    observed - true means you can see it RIGHT NOW.
    conf     - how much to trust this belief. age - ticks since you last saw it.
    A low-conf, high-age entity is a MEMORY, not a sighting. It may be wrong.
- affordances: the only actions available to you this tick. Choose from these.

CHOOSING WELL
- Attend to whichever drive is furthest from comfortable; do not let one starve
  while you service another.
- To eat you must be standing ON the food, not beside it. If it is not underfoot,
  `goto` it first.
- Waiting restores fatigue, and waiting on a heat source restores warmth.
- If nothing is urgent, it is worth going to LOOK at something you are no longer
  confident about. Refreshing a stale belief has real value.
- Prefer finishing what you started over constant switching. Your actions run for
  many ticks after you choose them.

RESPOND with JSON matching the schema: an `action` (verb plus target or
duration), a `prediction` of whether the next moments will surprise you, and
optionally a short `note`. No prose outside the JSON."""


class Mind:
    """Wraps a provider and turns a Frame into a commitment."""

    name = "mind"

    def __init__(self, provider, verbose: bool = False):
        self.provider = provider
        self.verbose = verbose
        self.calls = 0
        self.errors = 0
        self.cost = 0.0
        self.tokens = 0
        self.cached_tokens = 0
        self.last_note = ""
        self.last_prediction = None

    # --- prompt -----------------------------------------------------------
    @staticmethod
    def system_prompt() -> str:
        """Byte-identical every call. Do not interpolate anything here."""
        return SYSTEM

    @staticmethod
    def user_prompt(frame) -> str:
        d = frame.to_dict()
        # Trim to what is actionable: a huge frame costs tokens on every call
        # and the far field cannot be acted on anyway.
        d["entities"] = d["entities"][:14]
        return "FRAME:\n" + json.dumps(d, sort_keys=True, separators=(",", ":"))

    # --- decision ---------------------------------------------------------
    def choose(self, frame, wm, soma, rng):
        """Called only when the gate opens. Returns a policy-shaped dict."""
        self.calls += 1
        out = self.provider.complete(
            self.system_prompt(), self.user_prompt(frame), SCHEMA
        )
        self.tokens += out.usage.prompt_tokens + out.usage.completion_tokens
        self.cached_tokens += out.usage.cached_tokens
        self.cost += out.usage.cost(self.provider.model)

        if out.error or not out.parsed:
            self.errors += 1
            return self._fallback(frame, soma)

        action = (out.parsed or {}).get("action") or {}
        verb = action.get("verb")
        if verb not in ("goto", "eat", "wait"):
            self.errors += 1
            return self._fallback(frame, soma)

        self.last_note = (out.parsed.get("note") or "")[:200]
        self.last_prediction = out.parsed.get("prediction")

        choice = {"verb": verb}
        if action.get("target"):
            choice["target"] = action["target"]
        if verb == "wait":
            choice["duration"] = int(action.get("duration") or 20)

        # Validate against affordances: a hallucinated target is an error path,
        # not something to pass to the motor and discover later.
        if verb in ("goto", "eat"):
            legal = {
                (a["verb"], a.get("target"))
                for a in frame.affordances if a["verb"] == verb
            }
            if (verb, choice.get("target")) not in legal:
                self.errors += 1
                return self._fallback(frame, soma)
        return choice

    @staticmethod
    def _fallback(frame, soma):
        """When the model fails, the body still has to do something sensible.
        A dead tick is worse than a dull one."""
        drive, _ = soma.worst()
        kinds = {"energy": "food", "warmth": "warmth"}.get(drive)
        if kinds:
            cands = [e for e in frame.entities if e.kind == kinds]
            if cands:
                cands.sort(key=lambda e: e.range)
                return {"verb": "goto", "target": cands[0].id}
        return {"verb": "wait", "duration": 20}

    def stats(self) -> dict:
        return {
            "calls": self.calls,
            "errors": self.errors,
            "tokens": self.tokens,
            "cached_tokens": self.cached_tokens,
            "cost_usd": round(self.cost, 4),
            "model": self.provider.model,
        }
