# Project Updates

Running log of progress after the plan was finalized. Newest first. The plan itself ([`PLAN.md`](PLAN.md)) is the stable design doc; this file is the changelog of what actually happened.

---

## 2026-08-07 — Memory viewer, cleanup, and a go-live checklist. **61/61 green.**

Paused before the live run, as asked. Cleaned up everything outstanding.

### Memory viewer

Three stores rendered in the viewer, synced to the scrubber, showing what the agent held **at that moment** — not merely its final state. Episodes carry both when they were stored and when they were forgotten, because a bounded memory's forgetting is half of what it does, and a panel that only shows survivors hides it.

- **episodic** — ranked by surprise, with entity tags; recalled memories highlighted; recently-forgotten ones struck through
- **semantic** — distilled facts with support counts and when they were learned
- **procedural** — what actually worked, e.g. `energy · goto food_c · 5/6 worked`, `energy · goto lm_corner · 0/1`

A typical 3000-tick run: **200 held, 611 forgotten, 8 facts, 15 procedural entries.** Episode text was also rewritten — it had been repeating the tick and entity list already shown around it, which read as noise.

### Standing issues fixed

- **Note/action mismatch in the stream.** When the mind's choice was rejected as illegal, the note still described the *rejected* intention — "making for warmth_b" above an action of "waited". Fallbacks now replace the note with what actually happened and why.
- **The mind chose from the wrong list.** The mock selected targets from raw perception rather than from *affordances*, so withdrawn targets were picked and rejected — ~50 errors per run. Now **0 errors across every seed**; choosing only from what perception offers is the design principle, and it wasn't being honoured.
- **600 ticks burned on one unreachable target.** `MAX_TICKS` was 300 in a room crossable in ~50 steps, and nothing prevented an immediate retry. Now 120, with failed targets withdrawn from affordances for a cooldown — 40 ticks for food and warmth, since locking the agent out of *food* over a transient routing failure risks starving it for a non-reason.
- **Sedentary agent / oscillating agent.** Two failed attempts at the same problem: requiring wander targets be *out of sight* made it never leave its starting room (so the two agents never met); ranking by distance or staleness made it ping-pong between two landmarks. The real signal was where it had not *been* — `traffic` was already tracked for confabulation decay and is now surfaced on the frame as `visits`.
- **Stream readability** — consecutive identical entries collapse with a count (`×8`), so habitual repetition no longer buries the moments that matter.
- **Test suite honesty.** Three tests were passing for the wrong reasons or failing for irrelevant ones: the calibration test depended on the *old* erratic behaviour (it now uses an explicit roaming policy, since calibration is a property of the belief machinery, not of a policy); the commitment-failure test waited for a natural failure, which became rare once the agent stopped chasing things it could never catch (failure is now provoked deterministically); and the H4 multi-agent test was measuring sampling noise at 5 seeds (now 8, ratio 1.109 with n=314).

### Health after cleanup

Across 5 seeds × 6000 ticks: **energy 0.71, warmth 0.62, fatigue near setpoint, 0 errors**, ~55 ticks per decision.

### [`GOING_LIVE.md`](GOING_LIVE.md)

Written for the first real run. Ordered checklist, most-likely-to-break first: verify model IDs still resolve (Together gives 2–3 weeks' deprecation notice, and the docs disagree with themselves on `api.together.ai` vs `.xyz`), a 40-tick smoke test, confirm prompt caching is actually hitting (the difference between the modelled cost and ~4× it), then the rate governor, then the capability sweep. Budget: start at 40 ticks, ~1–3 calls.

---

## 2026-08-07 — Conscious stream. **Rendering it found four bugs the metrics hid.**

The event log said *what* happened; it couldn't say *why*. Every decision now records its causal chain — what woke the agent, what it was feeling, what was in view, what it chose, what it told itself, and what it expected — and `narrate.py` turns that into readable prose. Available in the viewer (synced to the scrubber, filterable by kind) and in the terminal via `--stream N`.

A thought now reads:

```
* t2323   ate food_a
          finished what it was doing; exhausted (fatigue 0.99)
          saw: food_a 0 away, r00 5 away, c02 6 away
          "hungry at 0.44 and food_a is underfoot - eating now"
          expected surprise: low
= t2323   finished eating food_a
! t2323   ate food_a — energy restored
```

Entry kinds: **thought** (the mind was actually consulted), **habit** (acted without thinking), **outcome**, **percept**, **speech**, **sleep**.

### The stream immediately exposed four behavioural bugs

None were visible in the aggregate metrics; all were obvious within seconds of reading the agent's own account.

1. **Walking to where it already stood.** "set off toward c00 … arrived after 0 ticks", repeatedly. Going somewhere you already are is not an available action — `goto` is no longer offered for a target underfoot.
2. **Chasing things that move.** 43 of ~70 decisions were spent pursuing critters, 150–250 ticks each, which can never be pinned down. Fixed properly rather than by blacklist: **epistemic value must account for how long the answer lasts.** Verifying a landmark buys thousands of ticks of certainty; verifying a critter buys one. Pursuit of anything mobile is also now capped at 60 ticks.
3. **Oscillating between two landmarks**, then between two adjacent objects, forever — because "wander to the farthest/oldest visible thing" flips back and forth. **Wandering means going somewhere out of sight;** walking toward something already visible gains no information. If nothing new is in view, settle instead.
4. **Sleep formed zero facts.** Episodes were tagged only with entities whose *existence* changed, so most had no subject at all and consolidation had nothing to count. Episodes are now tagged with what they were about.

### The fixes improved every headline number

| condition | calls | vs every-tick | score |
|---|---|---|---|
| every_tick | 5000 | 1.0× | 0.634 |
| idle_only | 237 | 21.1× | 0.642 |
| **gated** | **64** | **78.3×** | **0.678** |

**H1 strengthened: 78.3×** (was 60.9×), and gated is now strictly best on *both* axes — fewest calls *and* highest score, where before it traded a little behaviour for a lot of calls. Sleep now forms ~12 facts per run.

That is the argument for the feature in one line: a legible conscious stream is not decoration, it is an instrument. Four real defects had been sitting inside passing tests and healthy averages.

---

## 2026-08-07 — **FIRST LIGHT.** P5 complete, viewer shipped, all hypotheses measured.

**50/50 tests green.** All six phases built. The homunculus runs.

### Final scorecard

| | Claim | Result |
|---|---|---|
| **H1** | Gating cuts LLM calls ≥50× without degrading behaviour | **Supported** — 60.9×, behaviour **+14.5%** |
| **H2** | Surprise-weighted consolidation beats recency and random | **Supported** — 0.197 vs 0.033 vs 0.010 |
| **H3** | Legible uncertainty produces epistemic action unprompted | **Supported** — 355 actions, 34% refresh |
| **H4** | Intent-rollout beats diffusion on animate entities | **Falsified** — −2%, four entity types |

Three of four supported; one falsified and reported as such. That ratio is the point — every hypothesis was tested against a baseline that could have killed it.

### P5: two minds, and H4's fairest test

Two fully independent agents share one world — separate belief, body, motor, memory. Neither reads the other's state; each sees the other only through its senses. Scripted movers advance exactly once per tick regardless of population, so physics doesn't depend on how many minds are present.

**Speech is an action, not a channel.** It costs a turn, it's only offered as an affordance when someone is plausibly in earshot, and it reaches only those within hearing range. It immediately caught a real bug: speech became a *habit* and the agent repeated the identical sentence four times running. One-shot acts (`say`, `eat`) are now excluded from habitual re-issue.

**H4 retested against a goal-persistent LLM agent** — the case rollout was supposed to win, since an agent walking to food has genuinely persistent intent unlike a random-waypoint critter. Result: **−2.0%**, and uniformly so across critter (−2.3%), resident (−1.2%), and agent (−2.0%). H4 is falsified robustly across four distinct kinds of moving entity. The learned-persistence mechanism detects this and collapses rollout to the baseline, costing ~2% instead of the 258% the naive version cost. That graceful degradation is the salvageable result.

### Viewer

Self-contained HTML, no server or build step. It renders the thing that matters: the agent's **true position against its believed position**, side by side, plus the surprise trace with a tick mark wherever the mind was actually consulted — so you can see habitual action and deliberation as distinct events. Verified rendering in-browser at t2201: pose error 0.65 visible as offset circles, drives healthy, decision log distinguishing `idle` from `habit`.

### What the build actually taught

Nearly every real insight came from measurement contradicting my design, not from writing code:

- **Thinking every tick is harmful**, not merely wasteful — it scored worst of all conditions.
- **The surprise gate was backwards.** Surprise as an interrupt: 7× more calls, 30% worse. Surprise as a filter: 60.9× fewer calls, better behaviour. Same signal, opposite plumbing.
- **A single global constant cannot serve heterogeneous entities** — the displacement envelope had to be learned per entity, and the drift model had to calibrate per elapsed-time band, because short-gap samples outnumber long-gap ones ~200:1 and drown the signal.
- **Two of my own experiments were methodologically broken** and I caught them: an H2 probe ranked by the very score under test, and an H1 baseline that had silently degenerated into the condition it was meant to be compared against.

### Honest limits

- The mind runs against `MockProvider` throughout. `TogetherProvider` is written, wired, and cost-modelled, but **has not been run against a live key** — no real open model has driven this agent yet. Model IDs should be re-verified against `GET /v1/models` before trusting the cost figures.
- The task score is homeostasis-based. It shows the agent stays alive and competent; it does not show sophisticated reasoning.
- The world is deliberately trivial. Findings are about the architecture, not about intelligence.

**Next, if this continues:** a live Together run, the model sweep down the capability ladder (`GLM-5.2 → MiniMax-M3 → gpt-oss-20b`) to answer "how little mind does belief-maintenance need?", and the P4 sleep pass rewritten to use the LLM for genuine semantic distillation rather than counting.

---

## 2026-08-07 — P3 + P4 complete. **H1 supported. H2 supported.**

42 tests green. The mind, the gate, and memory are in. Everything runs against a deterministic `MockProvider`, so the full loop — prompt assembly, schema validation, parse path, cost accounting — is exercised offline with no key and no spend. `TogetherProvider` is written and ready for a real key.

### H1: SUPPORTED — 60.9× fewer calls, and behaviour *improved*

Three conditions on identical seeds, changing only the gating (6 seeds × 5000 ticks):

| condition | calls | vs every-tick | task score | Δ |
|---|---|---|---|---|
| every_tick | 5000 | 1.0× | 0.639 | — |
| idle_only | 240 | 20.9× | 0.782 | +22.5% |
| **gated** | **82** | **60.9×** | **0.732** | **+14.5%** |

The bar was ≥50× with no more than 10% degradation. We get **60.9× with behaviour 14.5% better**.

**Honest decomposition:** most of the saving is *durative action* (20.9×), not the surprise gate; the gate contributes the remaining ~2.9×. Reporting only "60× from gating" would credit the gate with the motor's work.

**An unexpected finding: thinking every tick is actively harmful** (0.639, the worst score). The agent reconsiders constantly and never completes anything. More cognition is not better cognition.

**I built the gate backwards first, and the measurement caught it.** My initial version used surprise as an *interrupt* — abort the current action to think. That was catastrophically bad: **7× more calls and 30% worse behaviour**, monotonically worse at every threshold setting I swept. Abandoning in-flight work costs more than reconsidering gains, and each interrupt cascaded into extra decisions. The correct reading of System 1 / System 2 is the opposite: surprise should *remove* calls, not add them — at a natural decision point, if nothing surprising has happened, repeat the standing plan **without consulting the mind at all**. Interrupts are now off by default and the gate is a filter, not a trigger.

Also fixed: the accumulator saturated. With decay 0.97 and typical scalar ~1, steady state is ~33, so any sane threshold fired every tick. Surprise now accumulates as *excess over the running background* — deviation from what is normally surprising in this world.

### H2: SUPPORTED — surprise-weighted retention beats recency and random

Recall@k for notable events under a bounded episodic store (5 seeds):

| capacity | surprise | recency | random |
|---|---|---|---|
| 40 | **0.154** | 0.015 | 0.031 |
| 60 | **0.197** | 0.033 | 0.010 |
| 120 | **0.183** | 0.097 | 0.063 |

Wins at every capacity, with the gap narrowing as room grows — the mechanism behaving as it should.

**Methodology correction worth recording.** My first version ranked probe events *by surprise* — which is circular, since the surprise policy retains exactly those items. It reported a flattering 14.6× win. Probes are now selected on an objective criterion (food consumed, entities appearing/vanishing), sampled uniformly. The honest margin is smaller and still decisive.

**Next:** P5 — a second agent, speech, and theory of mind. Then the replay viewer.

---

## 2026-08-07 — P2 complete: drives and durative action

22/22 tests green. The agent now has a body and sustains itself: mean energy **0.63**, warmth **0.66** (setpoints 0.75/0.70), 5–6 meals per 8000 ticks, across all seeds. Affect is computed from drive error, not injected.

**Durative action works, and this is the number that matters for P3:** deliberation happens once every **~65 ticks**, up from every 6. A `goto` runs 40+ ticks against the *believed* map with zero decisions. Without this the surprise gate could never pay for itself.

**Five bugs, each found by measurement rather than inspection.** Worth recording because every one was invisible in the code and obvious in the data:

1. **Livelock, 715 collisions in one run.** `_step_goto` reset the stuck counter every time it *emitted* a move, so a wedged agent never reached the escape threshold. Progress, not emission, now clears it.
2. **"Already there" read as "unreachable."** A*, asked to path to the cell you're standing on, returns a one-cell path; `path[1:]` is empty; `start()` marked it `FAILED: no_path`. The agent standing *on* food concluded it couldn't reach food.
3. **Eating thin air, 225 times.** The `eat` commitment reported `DONE` unconditionally. The agent stood *beside* food, "ate" repeatedly, and starved. Now the world's verdict decides the outcome, and the policy requires standing on the exact cell.
4. **Stale-belief starvation.** After eating both sources the agent believed no food existed and filtered them out forever — starving next to food that respawned 400 ticks later. Fixed by epistemic action: when you need something and believe none exists, go *look*.
5. **Occlusion misread as absence.** Disconfirmation fired on anything expected-but-unseen, so food behind a wall was disconfirmed out of existence. Absence of evidence is only evidence *with line of sight*.

**Two architectural additions earned by those failures**, both principled rather than patches:
- **Bump-based localization.** A collision says "there is a wall in direction *d*" — combined with the known floorplan, that constrains where the agent can be. Snapping to the nearest consistent cell is how it recovers when no landmark is in view.
- **Collision memory.** Cells recently bumped are treated as blocked for 60 ticks, so replanning routes around them instead of retrying the identical failing step. Collisions fell from ~3000 per run to under 10.

**Drift model is now self-calibrating per (class, elapsed-time band).** A single per-class scale was structurally blind to Δt-dependent miscalibration — short-gap samples outnumber long-gap ones ~200:1 and drown the signal. Animate calibration spread went 4.21× → within tolerance.

**World calibration, stated plainly:** drive rates, food density and respawn were tuned so that a *competent* policy can hold homeostasis and an inattentive one cannot. A fixture where even optimal play starves measures how fast the agent dies, not whether drives ground behaviour. The agent was not tuned to pass; the world was calibrated to be survivable.

---

## 2026-08-07 — P1 complete: perception, belief, surprise. **H4 falsified.**

12/12 tests green. The belief stack runs: egocentric polar sensing with occlusion, path integration, landmark re-localization with loop closure, lazy confabulation, and normalized surprise.

**Calibration achieved (the P1 exit test).** Normalized error is now roughly stationary across elapsed-time buckets while raw error grows sharply — spread 1.35× (static), 1.66× (inert), 2.43× (animate). Getting there required a real modeling fix: expected error must combine *three* independent sources — `it moved` (class dynamics) + `I moved wrongly` (pose drift) + `my senses are coarse` (quantization variance, which grows with range). Omitting the last two made static entities look 9× more surprising than they were, because the agent was attributing its own error to the world.

**Two bugs found and fixed by measurement, not inspection:**
- Pose error was reaching 20+ units in a 24-wide world. Cause: blocked moves added unrecoverable error. But a bump is *felt* — proprioception should undo the predicted step. With bumps corrected, the only true drift source is an **unfelt slip** (2% of moves), which is exactly what landmark fixes exist to correct. Mean pose error is now 0.61, max 1.9.
- Landmark fixes were *injecting* error by snapping to a noisy trilateration from quantized bearings. Now blended (weighted by landmark count) and skipped entirely when the agent is already confident and the disagreement is within sensor noise. A fix should never make a good estimate worse.

### H4: NOT SUPPORTED

**Claim:** per-class drift + intent rollout predicts animate entities better than diffusion. **Result: it does not, in this world.**

| Entity | rollout | no-motion | Δ |
|---|---|---|---|
| critter (random waypoints) | 1.61 | 1.58 | −1.8% |
| resident (fixed routine) | 4.07 | 4.05 | −0.5% |

The investigation is the useful part:

1. Naive ballistic rollout was **258% worse** than baseline — unbounded extrapolation of a velocity that, at short Δt, is mostly quantization noise, flinging estimates through walls and off the map.
2. Bounding displacement by a saturating envelope + EMA-smoothed velocity brought it to −5.9%. Still losing.
3. Stratifying by tracking quality showed well-tracked entities were *worse* (−8%) than poorly-tracked (−4%) — which **rules out noisy velocity** as the cause. The heading estimate is fine; the intent simply isn't persistent across occlusion.
4. Added a `resident` archetype with genuinely persistent intent (commutes between anchors) to locate the boundary rather than tuning the world until the hypothesis passed. Residents improved but still didn't win — they dwell 20–60 ticks per anchor, so they're stationary most of the time.

**The salvage, and it's the real finding:** per-entity `persistence` is now *learned online* from whether rollout has actually been beating the baseline for that entity. It converged to ~0.05 for both archetypes — the system **correctly discovered that rollout doesn't help here and switched it off**, collapsing to the no-motion answer at a cost of ~1% instead of 258%. The architecture detected its own failing predictor. That graceful degradation is what the test suite asserts, not H4.

Rollout would need materially lower sensor noise, or entities whose intent persists *across the observation gaps*, to pay off. Worth revisiting in P5 when a second LLM agent — which has genuinely persistent goals — becomes the thing being predicted.

**Next:** P2 — drives and durative action, still no LLM.

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
