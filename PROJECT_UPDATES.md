# Project Updates

Running log of progress after the plan was finalized. Newest first. The plan itself ([`PLAN.md`](PLAN.md)) is the stable design doc; this file is the changelog of what actually happened.

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
