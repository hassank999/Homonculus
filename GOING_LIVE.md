# Going live on Together

Everything so far runs against `MockProvider`. **No real model has ever driven this agent.** This is the checklist for the first live run, in order, with the things most likely to break called out.

## 0. Before spending anything

```bash
export TOGETHER_API_KEY=...          # never commit it; .gitignore covers *.key/.env
python -c "import httpx; print('httpx ok')"
```

`TogetherProvider` needs `httpx`; it is the only runtime dependency the live path adds.

## 1. Verify the model IDs are still real

This is the single most likely thing to be wrong. The IDs in `provider.MODELS` were verified on 2026-08-07, and Together gives serverless endpoints **2–3 weeks of deprecation notice**. Check before trusting the cost model:

```bash
curl -s https://api.together.ai/v1/models \
  -H "Authorization: Bearer $TOGETHER_API_KEY" \
  | python -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)]" \
  | grep -Ei 'glm|deepseek|minimax|gpt-oss'
```

Cross-check each against `provider.PRICES`. A stale ID is a 404; stale pricing is a silently wrong cost model.

Also confirm the base URL: the research said `api.together.ai`, Vivarium's config uses `api.together.xyz`. `provider.TOGETHER_URL` uses the former — if it 404s, try the latter before debugging anything else.

## 2. Smallest possible real call

```bash
python -m homunculus run --ticks 40 --mind together --model primary --out runs/live0/e.jsonl --stream 20
```

40 ticks is roughly 1–3 LLM calls. What to check in the output:

- `errors=0`. Any error here is a schema or parse problem, not a model-quality problem.
- The stream notes read like reasoning, not filler.
- `cost=$…` is non-zero and plausible.

**If `errors > 0`, look at this first:** the schema uses `additionalProperties: false` and a closed `verb` enum. Not every open model honours JSON-schema constraints equally. `provider.py` already treats `finish_reason == "length"` as an error rather than letting truncated JSON through, and `mind.py` rejects targets that aren't in the frame's affordances. Check which is firing before assuming the model is bad.

## 3. Confirm prompt caching is actually hitting

This is the difference between the modelled cost and roughly 4× that.

```bash
python -m homunculus run --ticks 400 --mind together --out runs/live1/e.jsonl
```

Then check that `cached_tokens` is a large fraction of `prompt_tokens`. `Mind.stats()` reports both. If cached is ~0 across many calls, something in the prefix is varying — the system prompt must be byte-identical every call. `Mind.system_prompt()` is a constant by design; the guard test is `test_system_prompt_is_byte_stable`.

Note Together's cache is **shared-fleet, best-effort, short-lived, no TTL**. The cost model already assumes ~50% hit rate, not 100%.

## 4. Rate limits — the real risk

Together's limits are **dynamic and unpublished**, and their limiter specifically penalises bursts. Use the governor on the first real run:

```bash
python -m homunculus run --ticks 2000 --mind together --min-interval 0.4 --view
```

`--min-interval` enforces spacing between calls. Expect `429`/`503` if you omit it and the gate opens several times in quick succession. `TogetherProvider` already retries with exponential backoff on both.

## 5. Then the actual experiment

The question the architecture was built to ask — *how little mind does belief-maintenance need?* — is a sweep down the capability ladder:

```bash
for m in primary cheap floor; do
  python -m homunculus run --ticks 3000 --mind together --model $m \
    --out runs/sweep-$m/e.jsonl --view runs/sweep-$m/replay.html
done
```

`primary` = GLM-5.2, `cheap` = MiniMax-M3, `floor` = gpt-oss-20b. Compare final drive levels, `errors`, and cost. **The point where belief-maintenance breaks is the finding**, not a failure.

Caveat: `gpt-oss-20b` has **no cached-input pricing**, so its cost advantage is smaller in practice than the sticker suggests.

## 6. What to watch for that the mock cannot show

| Risk | Symptom | Where to look |
|---|---|---|
| Schema not honoured | `errors > 0`, notes empty | `provider.complete` parse path |
| Truncated JSON | `error="truncated"` | raise `max_tokens` (default 700) |
| Model ignores affordances | errors, agent stalls | `mind.choose` legality check |
| Reasoning leaks into content | garbage notes | `_strip_reasoning`; some models inline `<think>` |
| Cache never hits | cost ~4× modelled | prefix stability |
| Burst throttling | 429/503 | raise `--min-interval` |

## 7. Budget

Modelled at ~$1.40/hour on GLM-5.2 gated, ~$0.07/hour on the floor model. A 3000-tick run makes roughly 50–110 calls. **Start with `--ticks 40`**, confirm the numbers, then scale. Nothing here needs a long run to prove it works.
