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

# Verified against the live Together catalog (2026-08-07). Re-check against
# GET /v1/models before trusting the cost model — serverless endpoints get only
# 2-3 weeks of deprecation notice, so these are config, not constants.
MODELS = {
    "primary":  "zai-org/GLM-5.2",              # 1.40 / 0.26 cached / 4.40
    "alt":      "deepseek-ai/DeepSeek-V4-Pro",  # 1.74 / 0.20 / 3.48
    "cheap":    "MiniMaxAI/MiniMax-M3",         # 0.30 / 0.06 / 1.20
    "floor":    "openai/gpt-oss-20b",           # 0.05 / -    / 0.20
    "sleep":    "deepseek-ai/DeepSeek-V4-Flash",# 0.14 / 0.03 / 0.28
}

# $ per million tokens: (input, cached input, output).
PRICES = {
    "zai-org/GLM-5.2":               (1.40, 0.26, 4.40),
    "deepseek-ai/DeepSeek-V4-Pro":   (1.74, 0.20, 3.48),
    "MiniMaxAI/MiniMax-M3":          (0.30, 0.06, 1.20),
    "openai/gpt-oss-20b":            (0.05, 0.05, 0.20),
    "deepseek-ai/DeepSeek-V4-Flash": (0.14, 0.03, 0.28),
}

_THINK = re.compile(r"<think>.*?</think>", re.S)


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
                 max_tokens: int = 700, timeout: float = 60.0, retries: int = 3):
        self.model = model or MODELS["primary"]
        self.api_key = api_key or os.environ.get("TOGETHER_API_KEY", "")
        self.max_tokens = max_tokens
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
                    # rather than letting a half-object through.
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
        choice = self._decide(frame, speak_now=(self.calls % 12 == 0))
        parsed = {
            "action": choice,
            "prediction": {"expect_surprise": "low"},
            "note": "mock",
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
    def _decide(frame: dict, speak_now: bool = False) -> dict:
        drives = frame.get("drives") or {}
        ents = frame.get("entities") or []
        affs = frame.get("affordances") or []

        def nearest(kind):
            c = [e for e in ents if e.get("kind") == kind]
            c.sort(key=lambda e: e.get("range", 1e9))
            return c[0]["id"] if c else None

        if any(a["verb"] == "eat" for a in affs) and drives.get("energy", 1) < 0.6:
            t = next(a["target"] for a in affs if a["verb"] == "eat")
            return {"verb": "eat", "target": t}
        if drives.get("energy", 1) < 0.55:
            t = nearest("food")
            if t:
                return {"verb": "goto", "target": t}
        if drives.get("warmth", 1) < 0.5:
            t = nearest("warmth")
            if t:
                return {"verb": "goto", "target": t}
        if drives.get("fatigue", 0) > 0.55:
            return {"verb": "wait", "duration": 40}
        # Speech is available only when someone is plausibly in earshot; say
        # something occasionally so the channel is exercised.
        if any(a["verb"] == "say" for a in affs) and speak_now:
            other = next((e for e in ents if e.get("kind") == "agent"), None)
            if other is not None:
                return {"verb": "say",
                        "text": f"i see {other['id']} at bearing "
                                f"{other.get('bearing')} range {other.get('range')}"}
        stale = [e for e in ents if not e.get("observed") and e.get("conf", 1) < 0.6]
        if stale:
            stale.sort(key=lambda e: e.get("conf", 1))
            return {"verb": "goto", "target": stale[0]["id"]}
        gotos = [a for a in affs if a["verb"] == "goto"]
        if gotos:
            return {"verb": "goto", "target": gotos[0]["target"]}
        return {"verb": "wait", "duration": 10}


def build(kind: str = "mock", model: str | None = None, **kw) -> LLMProvider:
    if kind == "together":
        return TogetherProvider(model=model, **kw)
    if kind == "mock":
        return MockProvider(model=model or "mock/deterministic")
    raise ValueError(f"unknown provider: {kind!r}")
