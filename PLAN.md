# HOMUNCULUS — Project Plan

**Status:** signed off — in execution
**Date:** 2026-08-07
**Provider:** Together AI (serverless, open-source models) · model-agnostic behind `LLMProvider`

---

## 1. Concept

An embodied agent that maintains a *belief* about a world rather than reading the world directly.

Every tick, a cheap loop advances a small simulated world. The agent holds its own estimate of that world — position, entities, drives — and emits a prediction of what it expects to perceive next. The gap between prediction and observation is the **surprise signal**, and that one signal does four jobs:

1. **Attention** — which perception channels are worth surfacing
2. **Inference scheduling** — when to spend an LLM call at all
3. **Memory consolidation** — what gets written to long-term store
4. **Model correction** — updating per-entity dynamics ("things of this class move more than I assumed")

Unobserved parts of the world are not simulated in the agent's head. They are timestamped and **confabulated lazily on read**, with drift as a deterministic function of elapsed time and entity class. Memory and imagination are therefore the same generative pass, differing only in whether observation is clamping it.

The thesis in one line: **prediction error is sufficient as the central currency of an embodied agent, and gating cognition on it makes the whole thing affordable.**

### Why this is worth building

The generic version of this (observation stream → memory stream → action) is the Stanford *Generative Agents* architecture and is well-trodden. Three things here are not:

- One signal driving attention, memory, *and* inference scheduling
- Lazy confabulation with per-class drift, giving object permanence and epistemic curiosity for free
- Sleep as a real offline batch job, not an inline "reflection" step

---

## 2. Scope

### In

- Tick-discrete 2D gridworld, deliberately trivial (~20×20, a dozen entities, 1–3 rooms)
- One LLM-driven agent, plus scripted mover entities that exercise drift and intent-rollout
- The full cognitive stack: sensorium, world model, surprise, drives, memory, motor, scheduler
- Instrumentation: every tick logged, deterministic replay, a benchmark battery, a static HTML timeline viewer

### Out (deliberately)

- **Rich world.** The sim is a test fixture, not a product. No pathfinding beyond A*, no physics, no graphics beyond the replay viewer. Every hour spent making the world interesting is an hour not spent on the thesis.
- **Real-time.** `dt` is a logical tick decoupled from wall clock. This dissolves the inference-latency problem for v1 and lets experiments run faster than real time. Real-time is a Phase-6 concern if ever.
- **Learning weights.** No fine-tuning. All adaptation is in explicit state (drift model, memory, drives).
- **Multi-agent society.** One LLM agent in v1. Phase 5 adds a second; anything beyond that is a different project.

### Deferred but designed-for

Interfaces are shaped so these drop in later without rework: multiple LLM agents, real-time clock, richer sensory modalities, swapping the mind for a local model.

---

## 3. Falsifiable claims

The plan is only worth running if it can fail. Four hypotheses, each measurable on the benchmark battery:

| ID | Claim | Measurement | Fails if |
|----|-------|-------------|----------|
| **H1** | Surprise-gated inference cuts LLM calls ≥50× vs. per-tick, with no significant degradation on the task battery | calls/hour and task score, gated vs. ungated | Task score drops >10% at 50× reduction |
| **H2** | Surprise-weighted consolidation beats recency-only and random on a memory probe | recall accuracy on "where was X / what happened when Y" probes | No significant separation from recency baseline |
| **H3** | Legible uncertainty produces epistemic action without being prompted for it | rate of moves whose only payoff is variance reduction | Agent never looks at things purely to re-check them |
| **H4** | Per-class drift + intent rollout predicts other agents better than diffusion | prediction error on animate entities, rollout vs. diffusion baseline | Rollout is no better than diffusion |

H1 is the load-bearing one. If it fails, the architecture is interesting but not affordable, and the project pivots to "how cheap can the mind get" rather than "what can it do."

---

## 4. Functional breakdown

### 4.1 The Frame — the central interface

Everything hangs off getting this record right. It is what the sensorium produces, what the mind consumes, and what the world model predicts.

```
Frame:
  tick: int
  self:
    pose:        (x, y, heading)          # agent's own estimate, not ground truth
    pose_conf:   float                    # decays with dead reckoning, resets on landmark fix
    efference:   Action | None            # copy of own motor command last tick
  soma:                                   # interoception
    drives:      {name: (value, setpoint, delta)}
    valence:     float                    # signed drive error
    arousal:     float                    # magnitude + rate of change
  entities: [                             # egocentric polar, near-field first
      { id, kind, bearing, range, salience, age, conf, state }
  ]
  occupancy:    coarse grid               # topology only: walls, openings, reachable
  events: [ ... ]                         # discrete: sounds, contacts, speech heard
  affordances: [ (verb, target) ]         # generated from entities, prunes action space
  budget:       { ticks_since_last_call, action_status }
```

Notes that matter:

- **Entities are a polar list, not an ASCII grid.** Language models read 2D character grids badly — relative position, bearing, and distance all degrade. `(kind, bearing, range)` tuples are markedly more reliable.
- **`age` and `conf` are visible to the agent.** This is what makes `cup, kitchen table, 40°, last seen 400 ticks ago, low confidence` a representable thought — and it's the precondition for H3.
- **Resolution falls off with distance.** Fine near the body, coarse far away. Cortical magnification, and also token economy.
- **`affordances` is generated, not prompted.** An open `(verb, noun)` space is unboundedly large; perception pruning it is far better than instructing the model to behave.

### 4.2 Modules

| # | Module | Responsibility |
|---|--------|----------------|
| 1 | `world` | Tick-discrete gridworld. Entities, scripted movers, event bus. Deliberately dumb. |
| 2 | `sensorium` | World state → observation. Egocentric transform, log-distance falloff, event detection, efference-copy injection. |
| 3 | `worldmodel` | The agent's *belief*. Path integration for self-pose, landmark re-localization, lazy confabulation of unobserved entities, per-class dynamics. Emits predicted Frames. |
| 4 | `surprise` | Predicted vs. actual Frame → per-channel and scalar error. Feeds attention gating, the scheduler, memory write strength, and drift-model updates. |
| 5 | `soma` | Homeostatic drives with setpoints and autonomous drift. Computes valence/arousal. Grounds why anything matters. |
| 6 | `memory` | Working buffer (last N ticks verbatim), episodic store (embedding × recency × surprise retrieval), semantic store, procedural store. Plus the sleep/consolidation job. |
| 7 | `mind` | The LLM call, behind a model-agnostic `LLMProvider` interface (Together in v1, swappable). Prompt assembly, prefix-stability discipline, schema-constrained output parsing. Emits: action commitment, next-Frame prediction, optional speech/think/null. |
| 8 | `motor` | Durative action commitments with controllers. Start / progress / interrupt / fail. Emits efference copy. Closes its loop on the spatial channel, not on the LLM. |
| 9 | `loop` | The tick scheduler. Decides when to wake the mind. Owns the token/wall-clock budget **and a concurrency governor** — a smooth request rate with backoff, since Together's dynamic limiter penalizes bursts (esp. two agents starting at once). |
| 10 | `scope` | Instrumentation. Structured per-tick log, metrics, HTML timeline viewer. **Seeded determinism is our build** (Vivarium has none); the append-only event-log + paced-replay + seek-as-burst engine is lifted from Vivarium (§10). |

Sensorium and worldmodel stay separate on purpose: one is world→observation, the other is the agent's belief. Collapsing them is how you accidentally hand the agent ground truth.

### 4.3 Output formation

The mind emits a single structured object per call:

```
MindOutput:
  commitment:  { verb, target, params } | CONTINUE | NULL
  prediction:  PredictedFrame          # required — this is the whole architecture
  speech:      str | None              # speech is an action, not a parallel channel
  think:       str | None              # private, fed back, not emitted to world
```

`prediction` being mandatory is the key design decision. Without it there is no surprise signal and the rest of the system has nothing to run on.

---

## 5. Implementation plan

Six phases. Each has an exit test that must pass before the next begins — these double as Test Requirements if this later goes through the cell protocol.

### P0 — Harness (foundation)
Tick loop, trivial world, agent that acts randomly, full structured logging, deterministic seeded replay.
**Exit:** a 10,000-tick run replays byte-identically from its seed; the log can reconstruct any tick's full state.

### P1 — Perception & belief
`sensorium`, `worldmodel`, `surprise`. Path integration, landmark correction, lazy confabulation, per-class drift. No LLM in the loop.
**Exit:** with a scripted agent, spatial prediction error stays bounded while landmarks are visible and grows measurably during blind stretches; confabulated entity positions degrade at the rate their class specifies. **H4 measurable here.**

### P2 — Body & action
`soma` (drives, valence, arousal) and `motor` (durative commitments, affordance generation). Still no LLM.
**Exit:** a hand-coded reactive policy driven only by drive error produces legible behavior — seeks warmth when cold, rests when fatigued. `walk_to` completes, fails, and interrupts correctly across many ticks with zero mind calls.

### P3 — Mind (the loop closes)
`mind` + `loop`. LLM in the loop, surprise-gated. Prompt assembly with a stable cached prefix.
**Exit:** the agent runs 5,000 ticks autonomously, producing behavior explicable from its drive state, with the gate holding the call count under a stated budget. **H1 measurable here.**

### P4 — Memory
Four stores, retrieval scoring, and the offline sleep/consolidation pass.
**Exit:** memory probe battery passes above the recency-only baseline; ablating episodic memory measurably degrades task performance. **H2 measurable here.**

### P5 — Second agent
Theory of mind (intent rollout for animate entities), speech as an action, a second LLM agent.
**Exit:** each agent's model of the other predicts its position better than diffusion; speech influences the other's behavior. **H3 verified and tuned here.**

---

## 6. Provider, models, and cost

**Provider: Together AI serverless inference. Models: open-source.** This is now a fixed constraint, and it changes one thing from a convenience into a load-bearing requirement.

### Model interchangeability is a hard requirement, not a later A/B

On a single-vendor closed model, swapping models is a string change. On open models it's the core experimental variable: the interesting question becomes *how much mind does the architecture actually need?* — does surprise-gating let a 70B model do what we'd assumed needed a frontier model, and where's the floor below which belief-maintenance collapses? That question is only answerable if swapping the mind is trivial and the rest of the system is model-agnostic.

So `mind` sits behind a **provider interface** from P0, not P3:

```
LLMProvider (protocol):
  complete(system, messages, schema, params) -> (parsed_obj, usage, raw)
  # implementations: TogetherProvider, (later) a local-model provider, a mock

MindConfig:
  model_id:        str          # swept in experiments, never hardcoded in the loop
  effort_params:   {...}        # per-model; reasoning models expose thinking, others don't
  schema:          JSON schema  # structured output is mandatory — see below
```

Two implications the code must honor:

- **No provider or model detail leaks above this interface.** `loop`, `surprise`, `memory`, `motor` never know which model is behind the mind. A run's model is a config value logged into `scope`, so every replay records which mind produced it.
- **Structured output is non-negotiable and provider-specific.** Every tick must return a parseable `MindOutput`. Together exposes schema-constrained decoding via `response_format: {type: "json_schema", ...}` on a specific subset of models — the sweep is **restricted to that subset**, because a parse failure mid-tick is a dead agent. Two Together-documented failure modes the provider layer must handle: JSON truncated when `finish_reason == "length"` (size `max_tokens` generously and treat truncation as an error), and — Together's own recommendation — belt-and-braces the constraint by also pasting the schema into the prompt and instructing JSON-only output. Every response is validated against the schema; a violation is an error path, not a shrug.

### Provider-layer facts (verified against Together docs, 2026-08-07)

These are Together specifics the `TogetherProvider` implementation must get right; none leak above the interface.

- **API is OpenAI-compatible.** `POST https://api.together.ai/v1/chat/completions`; the `openai` SDK works with a `base_url` override, or use the first-party `together` package. Auth: `TOGETHER_API_KEY`. `seed` is best-effort only — **no cross-replica determinism**, so sim reproducibility must come from *our* seed + logged raw responses, never from the model.
- **Reasoning tokens land in a non-standard place.** Together returns chain-of-thought in `message.reasoning` (not OpenAI's nested object; DeepSeek-R1-family instead inlines `<think>` tags in `content`). The provider strips/segregates this so `mind` only ever sees the parsed `MindOutput`. Reasoning composes cleanly with structured output: CoT in `reasoning`, schema-valid JSON in `content`.
- **Cache-hit accounting is inconsistently reported** — some models nest it at `usage.prompt_tokens_details.cached_tokens`, others use a flat `usage.cached_tokens`. Read both or silently log zeros.
- **Pin model IDs in config, not code.** Serverless endpoints get only 2–3 weeks' deprecation notice. `scope` logs the exact ID per run.

### Model roster

Verified against the live Together catalog. IDs post-date my training cutoff, so **before hardcoding the cost model, re-verify against `GET /v1/models` with the actual key** — the docs and pricing page already disagree in places. The three roles:

| Role | Model | In / cached-in / out ($/1M) | Why |
|------|-------|------------------------------|-----|
| Tick-loop mind (primary) | `zai-org/GLM-5.2` | 1.40 / 0.26 / 4.40 | Strong reasoning, `response_format` support, prompt caching, and interleaved thinking between tool calls — the best fit for an agentic loop. `deepseek-ai/DeepSeek-V4-Pro` (1.74 / 0.20 / 3.48) is the alternate. |
| Sweep floor (capability probe) | `MiniMaxAI/MiniMax-M3` → `openai/gpt-oss-20b` | 0.30/0.06/1.20 · 0.05/–/0.20 | MiniMax is cheap but capable with SO + caching + hybrid reasoning; gpt-oss-20b is the true floor (SO + `reasoning_effort`, but **no** cache pricing). The sweep walks down this ladder until belief-maintenance breaks — that break point is a finding, not a failure. |
| Sleep / consolidation | `deepseek-ai/DeepSeek-V4-Flash` | 0.14 / 0.03 / 0.28 | Cheapest capable model with SO + caching. Offline and latency-insensitive; runs async off-peak. **Not** via the batch endpoint — see below. |

The whole roster is the experiment. `model_id` is swept, never assumed; H1 is measured on the primary and re-measured down the floor ladder to answer "how little mind does this need?"

### Prompt caching — verified: automatic and steep

Good news that improves the cost model. Together caching is **always on, no header or toggle**, and bills the **longest matching prefix** at the cached rate — exactly our shape (big stable prefix, small changing suffix), ~85–90% off input on cache-supporting models (GLM-5.2 $1.40 → $0.26; DeepSeek-V4-Flash $0.14 → $0.03).

The prompt is assembled in strict stability order to exploit it:

```
[ world rules, persona, action grammar, drive semantics ]   ← stable prefix, billed cached when warm
[ retrieved memories ]                                       ← per-call, small
[ current Frame ]                                            ← per-call, small
```

- **Nothing volatile in the prefix.** No tick counters, timestamps, or UUIDs in the system prompt — the first divergent byte ends the cached span.
- **Deterministic serialization everywhere.** Sorted keys, no set iteration.

Two constraints that keep me honest about it: the cache is **shared-fleet, best-effort, short-lived, no TTL, no guaranteed hit** — so I model at a **conservative ~50% hit rate, not 100%** — and only cache-supporting models get the discount (gpt-oss-* and Llama-70B-Turbo do not). Even so, caching is now a real ~2× lever on top of the gate's ~100×, where the last revision assumed none.

### Batch API — discount doesn't apply to us

Together's 50% batch discount covers only six legacy models; every modern model, including our consolidation pick, runs at standard rate, and several (DeepSeek/Kimi) are excluded from batch entirely. So consolidation does **not** go through the batch endpoint — it just runs as ordinary async calls off the critical path. No loss: consolidation cost is rounding error either way.

### The numbers

Assumptions, argue with them: ~2,000-token stable prefix, ~400 tokens fresh input, ~400 tokens output. Primary model GLM-5.2, ~50% cache-hit rate on the prefix.

| Scenario | Model | ~Cost/call | Calls/hour | Cost/hour |
|----------|-------|-----------|-----------|-----------|
| Ungated at dt=100ms | GLM-5.2 | ~$0.004 | 36,000 | **~$150** — non-starter |
| Surprise-gated ~1:100 | GLM-5.2 | ~$0.004 | ~360 | **~$1.40** |
| Gated, sweep floor | MiniMax-M3 | ~$0.0012 | ~360 | **~$0.45** |
| Gated, true floor | gpt-oss-20b | ~$0.0002 | ~360 | **~$0.07** |

An 8-hour overnight run on the primary model lands around **$8–12**; on the cheap floor, under **$4**. Consolidation is rounding error at any tier.

The ~100× gap between the first two rows *is* H1, and it holds across every model in the roster — it's a property of the gate, not the vendor.

---

## 7. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **LLM spatial reasoning is too weak for H4** | High | Polar entity lists over grids from day one; measure spatial competence in P1 as a standalone probe before building H4 on top of it |
| **Surprise threshold tuning is fiddly** | High | Make it adaptive: a controller on the threshold targeting a chosen calls-per-1000-ticks rate. Never a hand-tuned constant. |
| **Prompt bloat destroys the cost model** | High | Hard token budget per call, enforced in `mind`, logged per tick. A run that exceeds it fails loudly. |
| **Open model too weak to hold belief state** | High | This is a research finding, not just a risk — the sweep *measures* the floor. Provider interface makes the swap trivial; disqualify any model that can't guarantee schema-valid output before quality even enters the question. |
| **Together rate limits are dynamic & unpublished; bursts are penalized** | High | Their limiter punishes exactly our worst pattern — parallel agent calls at session start. `loop` owns a **concurrency governor** with a smooth request rate and exponential backoff on 429/503; never a token-bucket sized to a published number (there isn't one). The surprise gate helps here too: fewer calls, less burst. |
| **Structured output unreliable on some models** | Medium | Provider layer validates every response against the schema; a violation (or `finish_reason == "length"` truncation) is an error path. Sweep restricted to `response_format`-capable models; schema also pasted into the prompt per Together's own guidance. |
| **Cache hit-rate lower than modeled** | Low | Shared-fleet cache is best-effort with no guaranteed hit; cost modeled at ~50%, not 100%. If it underperforms, the gate still carries the ~100×. |
| **Confabulation is hard to debug** | Medium | Lazy evaluation + deterministic seed + full replay means any confabulated value is reproducible and attributable |
| **Scope creep into "build a good sim"** | Medium | World module has a stated LOC ceiling and no feature backlog. If the world is interesting, we've failed. |

---

## 8. Deliverables

1. **`homunculus/`** — Python package, the ten modules above, typed Frame schema
2. **CLI runner** — `python -m homunculus run --scenario kitchen --ticks 5000 --seed 42`
3. **Replay viewer** — self-contained HTML timeline: tick scrubber, belief-vs-truth overlay, surprise trace, call markers, drive plots
4. **Benchmark battery** — the H1–H4 measurements as runnable scripts with baseline comparisons
5. **Findings write-up** — what held, what didn't, what the numbers were

Phase exit tests map cleanly onto Test Requirements if you want to route the build through `/cell` rather than build it directly.

---

## 9. Settled decisions

1. **Tick-discrete, not real-time.** Removes inference latency as a v1 problem entirely and lets experiments run faster than wall clock.
2. **Python 3.14** (already installed here), numpy for grids.
3. **Together AI serverless, open-source models.** Model swap is a first-class experimental axis (§6), not a convenience.
4. **Model interchangeability from P0.** `mind` sits behind `LLMProvider`; nothing below it knows the model. This is what makes the capability-floor sweep possible.
5. **Two LLM agents are in scope** (per sign-off). Scripted movers still stand in through P1–P4 so drift and intent-rollout are exercised early; the second *LLM* agent lands in P5.
6. **The world stays deliberately boring.** The decision most likely to feel wrong in week two and most likely to be right.
7. **Session length is a non-question** (per sign-off): the replay tool plus model interchangeability make "how long do you watch" irrelevant to the architecture — any run is inspectable after the fact, and the cost model is driven by the gate ratio, not by wall-clock duration.

---

## 10. Vivarium reuse — decided

Evaluated `Documents/Code/Vivarium` (an agent-orchestration lab, ~27k LOC Python + an Electron console). It is **not** a skeleton to build inside — building HOMUNCULUS as a Vivarium "structure" would mean fighting a framework with no tick, no world state, and a filesystem-jailed code-writing agent model. But two of its layers are dependency-light, domain-free, and save real time. **Decision: vendor specific files, don't take a subtree, don't depend on the package.**

### Lift as-is (vendor the files)

| What | Files | Why it's a gift |
|------|-------|-----------------|
| **LLM client layer** — this *is* our `TogetherProvider` | `adapters/core.py`, `adapters/openai_compat.py`, `contracts/model.py`, `contracts/ids.py` (~1,250 LOC, **httpx-only**) | Real provider abstraction, zero domain coupling, SSE streaming, `reasoning`-field handling, tool-call accumulation, HTTP error taxonomy with retry flags. **Already configured against Together AI** in Vivarium's own config. This is most of §6's provider interface already built. |
| **Scripted mock provider** | `adapters/mock/*` (stdlib-only local HTTP server) | Test the whole tick loop without burning tokens or hitting Together's limiter. Directly serves the `mock` provider in `MindConfig`. |
| **Paced replay engine** | `store/replay.py` (~184 LOC, pure) | Yields events by recorded inter-event gaps with pause/seek/speed; only touches `Event.ts`/`.seq`. Hard-won Windows timer-granularity handling included. |

### Lift with edits

| What | Edit needed |
|------|-------------|
| **Event-log writer/reader** (`store/writer.py`, `store/reader.py`) | Generic append-only JSONL with crash-tolerant truncation, fsync policy, live fan-out. Replace the 46-type Vivarium event spec with our Frame/tick schema. |
| **Table-driven event validator** (`contracts/events.py`) | Keep the ~150-line generic validator; delete the Vivarium vocabulary table, write ours. |
| **Seek-as-burst replay session** (`service/replay.py:182-196`) | The single best idea in the repo for our timeline UI: a seek to seq S doesn't move a cursor, it re-bursts events 1..S and re-folds deterministically, then resumes. Adopt the semantic. |
| **Cost metering** (`metering` PriceTable/money) | Pure Decimal-over-`Usage` math; wire in Together's price table from §6. |

### Build ourselves — Vivarium has nothing here (and these are the thesis anyway)

- **Tick scheduler** — Vivarium is asyncio real-time event-driven; no fixed-step loop exists. `loop` is ours.
- **Memory + retrieval** — no embeddings, no vector store, nothing. `memory` is ours.
- **Structured output** — Vivarium has *no* `response_format`; it gets JSON the hard way (write-file → read-back → validate → retry). We use Together's `response_format` directly; the retry-with-error-fed-back *pattern* is worth stealing as a fallback for models without constrained decoding.
- **Seeded deterministic re-execution** — **the important caveat.** Vivarium's "deterministic replay" means deterministic *re-fold of a recorded log*, not deterministic *re-run from a seed*. There is no RNG seeding anywhere in it. Our P0 exit test — "10,000 ticks replay byte-identically from seed 42" — is a genuinely different guarantee and is **entirely our build**. The two are complementary: our seed makes the *world* reproducible; Vivarium's replay engine makes a *recorded run* re-inspectable. We need both and only get the second for free.

### Consequence for §6

The `TogetherProvider` is no longer written from scratch — it's Vivarium's `openai_compat` adapter plus a `response_format` addition and schema validation. One reconciliation note: I earlier wrote "use the `openai` SDK with a base_url override." We're **not** doing that — Vivarium's adapter is hand-rolled httpx and it's the better base, since it already normalizes the `reasoning` field and won't fight us on Together's non-OpenAI response shapes. (Also flag: Vivarium's config uses `api.together.xyz`, the research used `api.together.ai` — verify which resolves before first run.)

### UI honest note

Vivarium's replay is a raw range-input slider wired to a server session — the *scrubber* is 30 lines of React, not a reusable timeline component, and its projection layer (1,470 LOC) is pure Vivarium domain. So our HTML timeline viewer (belief-vs-truth overlay, surprise trace, drive plots) is still built from scratch — but sitting on Vivarium's paced-replay + seek-as-burst engine underneath, which is the hard part.
