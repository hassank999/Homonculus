# Project Updates

Running log of progress after the plan was finalized. Newest first. The plan itself ([`PLAN.md`](PLAN.md)) is the stable design doc; this file is the changelog of what actually happened.

---

## 2026-08-07 — P0 harness complete, exit test passing

The harness is built and the P0 exit test passes for real (5/5, `python -m pytest`):

- **Byte-identical replay at 10,000 ticks from seed 42** — verified in-memory *and* on-disk (the file-bytes test guards against Windows CRLF translation).
- **Reconstruct any tick's full state** — replay folds concrete move-outcomes onto the initial snapshot with zero RNG in the read path; checked against live snapshots at ticks 0/1/500/4999/9999/10000.
- Seed sensitivity and lossless log roundtrip also green.

Modules (`homunculus/`): `rng` (hashlib-derived sub-streams — not builtin `hash()`, which PYTHONHASHSEED would randomize), `world` (20×20 room, scripted critter movers, wall/bounds blocking), `agent` (`RandomAgent` placeholder conforming to the future mind's `act(world, rng)` shape), `events` (canonical sorted-key JSON), `eventlog` (LF-only JSONL), `loop` (tick orchestration + scenario), `replay` (`state_at` fold), `__main__` (CLI). Tests in `tests/`.

Determinism design notes, since this was the crux: single seeded RNG with independent named streams, fixed intra-tick consumption order (agent → movers in sorted id order), sorted iteration everywhere outputs are produced, no wall-clock in the event stream. CLI verified: `python -m homunculus run --seed 42 --ticks 5000` → 5002 events.

**Next:** P1 — perception & belief. `sensorium` (egocentric polar observation with log-distance falloff), `worldmodel` (path integration for self-pose, landmark re-localization, lazy confabulation with per-class drift), `surprise` (predicted vs actual Frame). Still no LLM. Exit test: bounded spatial error while landmarks are visible, measurable growth during blind stretches, per-class drift calibration. H4 becomes measurable here.

---

## 2026-08-07 — Plan finalized, repo created

- `PLAN.md` complete and signed off. Concept, scope, four falsifiable claims (H1–H4), ten-module breakdown, six-phase build, risks, deliverables.
- **Provider decided:** Together AI serverless, open-source models, behind a model-agnostic `LLMProvider`. Model interchangeability is a hard requirement — the sweep across models is the experiment ("how little mind does belief-maintenance need?"), not a later A/B.
- **Model roster set** against the verified live Together catalog: primary `zai-org/GLM-5.2`, sweep floor `MiniMaxAI/MiniMax-M3` → `openai/gpt-oss-20b`, consolidation `deepseek-ai/DeepSeek-V4-Flash`. IDs to be re-verified against `GET /v1/models` before first run.
- **Cost model grounded:** Together prompt caching is automatic (~85–90% off input, longest-prefix match — our exact shape), modeled conservatively at ~50% hit rate. Gated tick loop ~$1.40/hr on the primary model; the ~100× gated-vs-ungated gap *is* H1. Batch discount doesn't apply to modern models — consolidation runs async at standard rate.
- **Vivarium reuse decided:** vendor the httpx LLM adapter (already Together-configured) as the base for `TogetherProvider`, plus the mock provider and the paced-replay + seek-as-burst engine. Build ourselves: tick scheduler, memory/retrieval, structured output, and seeded deterministic re-execution (Vivarium's "replay" re-folds a log, it does not re-run from a seed — different guarantee).

**Next:** P0 — harness. Tick loop, trivial world, random-acting agent, per-tick structured logging, deterministic seeded replay. Exit test: 10,000 ticks replay byte-identically from seed 42.
