"""LLM provider layer — model-agnostic by construction.

Model interchangeability is a hard requirement, not a later A/B (PLAN.md §6):
the experiment IS the sweep across models ("how little mind does belief
maintenance need?"). So nothing above this interface ever learns which model is
behind it, and `model_id` is config, never a literal in the loop.

Together specifics handled here and nowhere else:
  * OpenAI-compatible /v1/chat/completions, so a thin httpx client suffices.
  * Structured output via response_format={"type":"json_schema", ...}. The
    sweep is restricted to models supporting it — a parse failure mid-tick is a
    dead agent.
  * Reasoning arrives in a non-standard `message.reasoning` field (some models
    inline <think> tags in content instead). Stripped here.
  * Cache-hit accounting is reported inconsistently: some models nest it under
    usage.prompt_tokens_details.cached_tokens, others use a flat
    usage.cached_tokens. Read both or silently log zeros.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

TOGETHER_URL = "https://api.together.ai/v1/chat/completions"

# Verified live against GET /v1/models on 2026-08-08: all five IDs resolve and
# every input/output price below matches the catalog exactly.
#
# Note the `-0731` suffix on Flash. Writing the bare `DeepSeek-V4-Flash` was a
# 404 — the exact failure mode the go-live checklist predicted, and the reason
# these are config rather than constants. Serverless endpoints get only 2-3
# weeks of deprecation notice; re-verify before trusting the cost model.
MODELS = {
    "primary":  "zai-org/GLM-5.2",                   # 1.40 / 0.26 cached / 4.40
    "alt":      "deepseek-ai/DeepSeek-V4-Pro",       # 1.74 / 0.20 / 3.48
    "cheap":    "MiniMaxAI/MiniMax-M3",              # 0.30 / 0.06 / 1.20
    "floor":    "openai/gpt-oss-20b",                # 0.05 / -    / 0.20
    "sleep":    "deepseek-ai/DeepSeek-V4-Flash-0731",# 0.14 / 0.03 / 0.28
}

# $ per million tokens: (input, cached input, output).
PRICES = {
    "zai-org/GLM-5.2":                    (1.40, 0.26, 4.40),
    "deepseek-ai/DeepSeek-V4-Pro":        (1.74, 0.20, 3.48),
    "MiniMaxAI/MiniMax-M3":               (0.30, 0.06, 1.20),
    "openai/gpt-oss-20b":                 (0.05, 0.05, 0.20),
    "openai/gpt-oss-120b":                (0.15, 0.15, 0.60),
    "deepseek-ai/DeepSeek-V4-Flash-0731": (0.14, 0.03, 0.28),
}

_THINK = re.compile(r"<think>.*?</think>", re.S)

# Per-model request extras. Reasoning models spend the output budget on chain
# of thought BEFORE emitting the answer, so with a small max_tokens they get
# truncated mid-JSON and look incapable when they are merely misconfigured —
# gpt-oss-20b failed 46% of calls this way until `reasoning_effort` was set.
# The tick loop wants a decision, not an essay.
MODEL_EXTRAS: dict[str, dict] = {
    "openai/gpt-oss-20b":  {"reasoning_effort": "low"},
    "openai/gpt-oss-120b": {"reasoning_effort": "low"},
}


@dataclass
class Usage:
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0

    def cost(self, model: str) -> float:
        inp, cached, out = PRICES.get(model, (1.0, 0.5, 3.0))
        fresh = max(self.prompt_tokens - self.cached_tokens, 0)
        return (
            fresh * inp / 1e6
            + self.cached_tokens * cached / 1e6
            + self.completion_tokens * out / 1e6
        )


@dataclass
class Completion:
    parsed: dict
    usage: Usage = field(default_factory=Usage)
    raw: str = ""
    error: str | None = None


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str, schema: dict) -> Completion: ...


class ProviderError(RuntimeError):
    pass


def _extract_usage(payload: dict) -> Usage:
    u = payload.get("usage") or {}
    cached = u.get("cached_tokens")
    if cached is None:
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return Usage(
        prompt_tokens=int(u.get("prompt_tokens", 0) or 0),
        cached_tokens=int(cached or 0),
        completion_tokens=int(u.get("completion_tokens", 0) or 0),
    )


def _strip_reasoning(text: str) -> str:
    return _THINK.sub("", text or "").strip()


class TogetherProvider:
    """Thin httpx client for Together's OpenAI-compatible endpoint.

    Retries are bounded and backoff is exponential because Together's limiter is
    dynamic and unpublished, and it penalises bursts specifically — see the
    concurrency governor in loop.py.
    """

    name = "together"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 max_tokens: int = 1200, timeout: float = 60.0, retries: int = 3,
                 extra: dict | None = None):
        self.model = model or MODELS["primary"]
        self.api_key = api_key or os.environ.get("TOGETHER_API_KEY", "")
        self.max_tokens = max_tokens
        self.extra = dict(MODEL_EXTRAS.get(self.model, {}))
        if extra:
            self.extra.update(extra)
        self.truncations = 0
        self.timeout = timeout
        self.retries = retries
        self._client = None

    def _http(self):
        if self._client is None:
            try:
                import httpx
            except ImportError as e:                      # pragma: no cover
                raise ProviderError("httpx required for TogetherProvider") from e
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def complete(self, system: str, user: str, schema: dict) -> Completion:
        if not self.api_key:
            raise ProviderError("TOGETHER_API_KEY is not set")
        body = {
            "model": self.model,
            "messages": [
                # Stable prefix first so Together's automatic prefix caching can
                # bill it at the cached rate; the volatile Frame goes last.
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "mind_output", "schema": schema},
            },
        }
        body.update(self.extra)
        headers = {"Authorization": f"Bearer {self.api_key}"}

        last = None
        for attempt in range(self.retries):
            try:
                r = self._http().post(TOGETHER_URL, json=body, headers=headers)
                if r.status_code in (429, 503):
                    time.sleep(min(2 ** attempt, 8) + 0.1 * attempt)
                    last = f"http {r.status_code}"
                    continue
                r.raise_for_status()
                payload = r.json()
                choice = (payload.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                text = _strip_reasoning(msg.get("content") or "")
                if choice.get("finish_reason") == "length":
                    # Truncated JSON is unparseable; treat as an error path
                    # rather than letting a half-object through. Counted
                    # separately because a run full of these means the budget
                    # is wrong, not that the model cannot do the task.
                    self.truncations += 1
                    return Completion({}, _extract_usage(payload), text,
                                      error="truncated")
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as e:
                    return Completion({}, _extract_usage(payload), text,
                                      error=f"unparseable: {e}")
                return Completion(parsed, _extract_usage(payload), text)
            except Exception as e:                        # noqa: BLE001
                last = str(e)
                time.sleep(min(2 ** attempt, 8))
        return Completion({}, Usage(), "", error=f"failed after retries: {last}")


class MockProvider:
    """Deterministic offline provider.

    Exists so the entire tick loop — including the gate, the prompt assembly and
    the parse path — is testable without a key, a network, or spend. It answers
    from the Frame using the same information the real mind would see, so a run
    against it exercises every code path except the HTTP call itself.
    """

    name = "mock"

    def __init__(self, model: str = "mock/deterministic", seed: int = 0):
        self.model = model
        self.calls = 0
        self._seed = seed

    def complete(self, system: str, user: str, schema: dict) -> Completion:
        self.calls += 1
        try:
            frame = json.loads(user[user.index("{"):user.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            frame = {}
        # Gate speech on the call counter rather than the tick: with habitual
        # action, decisions are rare enough that a tick-based schedule would
        # essentially never coincide with one.
        choice, note, expect = self._decide(
            frame, speak_now=(self.calls % 5 == 0)
        )
        parsed = {
            "action": choice,
            "prediction": {"expect_surprise": expect},
            "note": note,
        }
        text = json.dumps(parsed, sort_keys=True)
        # Token counts approximate the real shape so cost accounting is
        # exercised end to end (prefix mostly cached, small fresh suffix).
        usage = Usage(
            prompt_tokens=len(system) // 4 + len(user) // 4,
            cached_tokens=len(system) // 4,
            completion_tokens=len(text) // 4,
        )
        return Completion(parsed, usage, text)

    @staticmethod
    def _decide(frame: dict, speak_now: bool = False):
        """Returns (action, note, expected_surprise).

        The note is the agent's own account of why — it is what makes the
        conscious stream readable, so the mock writes real reasoning rather
        than a placeholder.
        """
        drives = frame.get("drives") or {}
        ents = frame.get("entities") or []
        affs = frame.get("affordances") or []
        energy = drives.get("energy", 1.0)
        warmth = drives.get("warmth", 1.0)
        fatigue = drives.get("fatigue", 0.0)

        # Only ever choose from what perception actually offers. Selecting off
        # the raw entity list meant picking targets that had been withdrawn
        # (unreachable, or underfoot), which the mind then had to reject.
        reachable = {a.get("target") for a in affs if a["verb"] == "goto"}

        def nearest(kind):
            c = [e for e in ents
                 if e.get("kind") == kind and e.get("id") in reachable]
            c.sort(key=lambda e: e.get("range", 1e9))
            return c[0] if c else None

        if any(a["verb"] == "eat" for a in affs) and energy < 0.6:
            t = next(a["target"] for a in affs if a["verb"] == "eat")
            return ({"verb": "eat", "target": t},
                    f"hungry at {energy:.2f} and {t} is underfoot — eating now",
                    "low")

        if energy < 0.55:
            e = nearest("food")
            if e:
                fresh = "in sight" if e.get("observed") else \
                        f"remembered, {e.get('age')} ticks stale"
                return ({"verb": "goto", "target": e["id"]},
                        f"energy down to {energy:.2f}; {e['id']} is the closest "
                        f"food at range {e.get('range')} ({fresh})",
                        "low" if e.get("observed") else "medium")

        if warmth < 0.5:
            e = nearest("warmth")
            if e:
                # Standing on it already: linger rather than ask to walk here,
                # which is not an available action and would be rejected.
                if float(e.get("range", 99) or 99) < 1.0:
                    return ({"verb": "wait", "duration": 30},
                            f"cold at {warmth:.2f}; already on {e['id']}, "
                            f"staying put to warm through",
                            "low")
                return ({"verb": "goto", "target": e["id"]},
                        f"cold at {warmth:.2f}; making for {e['id']} to warm up",
                        "low")

        if fatigue > 0.55:
            return ({"verb": "wait", "duration": 40},
                    f"fatigue at {fatigue:.2f}; resting before doing more",
                    "low")

        # Speech is available only when someone is plausibly in earshot.
        if any(a["verb"] == "say" for a in affs) and speak_now:
            other = next((e for e in ents if e.get("kind") == "agent"), None)
            if other is not None:
                return ({"verb": "say",
                         "text": f"i see {other['id']} at bearing "
                                 f"{other.get('bearing')} range {other.get('range')}"},
                        f"{other['id']} is close enough to hear me; saying where I am",
                        "low")

        # Epistemic action, weighted by whether the answer will KEEP. Checking a
        # landmark buys lasting certainty; checking a critter buys one tick of
        # it, because the thing moves the moment you look away. Ignoring that
        # had the agent spending most of its decisions chasing critters it can
        # never pin down.
        # Residents commute, so verifying one is stale again almost immediately;
        # only genuinely stationary things repay the trip.
        LASTING = {"landmark", "food", "warmth", "item"}
        stale = [
            e for e in ents
            if not e.get("observed") and e.get("conf", 1) < 0.6
            and e.get("kind") in LASTING and e.get("id") in reachable
        ]
        if stale:
            stale.sort(key=lambda e: (e.get("conf", 1), e.get("range", 0)))
            s = stale[0]
            return ({"verb": "goto", "target": s["id"]},
                    f"nothing urgent, but only {s.get('conf'):.2f} sure about "
                    f"{s['id']} after {s.get('age')} ticks - worth a look since "
                    f"it should stay put",
                    "high")

        # Wandering must actually CHANGE the vantage point. A target is worth
        # the trip if it is out of sight, or far enough away that walking there
        # reveals something on the way. Requiring only "out of sight" made the
        # agent sedentary — it never left its starting room, so it never
        # discovered anything, and two agents never met. Requiring only
        # "farthest visible" made it oscillate between adjacent objects.
        by_id = {e["id"]: e for e in ents}
        worth = [
            a for a in affs
            if a["verb"] == "goto"
            and by_id.get(a["target"], {}).get("kind") in LASTING
            and (not by_id.get(a["target"], {}).get("observed", False)
                 or float(by_id.get(a["target"], {}).get("range", 0) or 0) >= 6.0)
        ]
        if worth:
            worth.sort(key=lambda a: (
                round(float(by_id[a["target"]].get("visits", 0) or 0), 1),  # least-visited
                by_id[a["target"]].get("observed", False),                  # then unseen
                -int(by_id[a["target"]].get("age", 0) or 0),                # then stalest
            ))
            pick = worth[0]["target"]
            return ({"verb": "goto", "target": pick},
                    f"all needs met (energy {energy:.2f}, warmth {warmth:.2f}); "
                    f"nothing new here, heading for {pick}",
                    "medium")
        return ({"verb": "wait", "duration": 25},
                "all needs met and nothing worth walking to; settling for a while",
                "low")


def build(kind: str = "mock", model: str | None = None, **kw) -> LLMProvider:
    if kind == "together":
        return TogetherProvider(model=model, **kw)
    if kind == "mock":
        return MockProvider(model=model or "mock/deterministic")
    raise ValueError(f"unknown provider: {kind!r}")
