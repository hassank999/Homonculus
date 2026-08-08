# HOMUNCULUS — orientation

Read this first. Then [`PLAN.md`](PLAN.md) for design, [`PROJECT_UPDATES.md`](PROJECT_UPDATES.md) for what happened (newest first).

## What this is

An embodied agent that maintains a **belief** about a world rather than reading it. Prediction error ("surprise") is the single currency: it drives attention, decides what enters long-term memory, corrects the drift model, and gates whether an LLM call happens at all.

Unobserved things are **confabulated lazily on read** — never simulated forward. `resolve(belief, now)` is a pure function of `(record, elapsed, class)`, which is why drift is auditable and time-travelable.

## State: live and working

All six phases built. **61 tests green.** Real Together models drive it; a 3000-tick run costs $0.005–$0.077 depending on model.

| | Claim | Result |
|---|---|---|
| H1 | Gating cuts calls ≥50× without degrading behaviour | **Supported** — 78.3×, behaviour better |
| H2 | Surprise-weighted consolidation beats recency/random | **Supported** |
| H3 | Legible uncertainty produces epistemic action | **Supported** — incl. unprompted, from a live model |
| H4 | Intent-rollout beats diffusion on animate entities | **Falsified** — −2% across 4 entity types |

## Run it

```bash
python -m homunculus run --ticks 3000 --mind mock --view      # offline, free
python -m homunculus run --ticks 3000 --mind together --model floor --view
python -m homunculus experiment all
python -m pytest -q                                            # ~3 min
```

`--mind mock` is a deterministic offline provider: full loop, no key, no spend. `--stream N` prints the conscious stream to the terminal.

**Windows note:** `TOGETHER_API_KEY` lives in User-scope env. Git Bash may not inherit it; PowerShell can load it explicitly:
`$env:TOGETHER_API_KEY = [Environment]::GetEnvironmentVariable('TOGETHER_API_KEY','User')`

## Architecture, in dependency order

`world` → `sensorium` (egocentric polar + occlusion) → `worldmodel` (belief: path integration, landmark + bump localization, lazy confabulation, loop closure) → `surprise` (**normalized**) → `soma` (drives) → `motor` (durative commitments, A*, collision memory) → `gate` (when to think) → `mind` (LLM, model-agnostic) → `memory` (4 stores + sleep) → `narrate`/`viewer`/`scope`.

`multi` runs two agents in one world. `experiments` holds the H1–H3 measurements.

## Things that are load-bearing and easy to break

1. **Normalized surprise.** Raw error is *not* surprise. Expected error combines three sources — *it moved* + *I moved wrongly* + *my senses are coarse*. Drop the last two and static entities look 9× more surprising than they are.
2. **The gate removes calls, it does not add them.** Surprise as an *interrupt* was measured 7× more calls and 30% worse. At a decision point, if nothing surprising happened, repeat the standing plan without consulting the mind.
3. **The mind chooses only from `affordances`.** Never from raw perception. Violating this produced ~50 errors/run.
4. **Byte-stable system prompt.** Together bills the longest matching prefix at the cached rate (40%+ hit rate observed). Any interpolated tick/timestamp/UUID above the boundary kills it.
5. **Determinism.** Seeded RNG with `hashlib`-derived sub-streams (never builtin `hash()` — `PYTHONHASHSEED` randomizes it), fixed intra-tick order, sorted iteration, LF-only writes.

## How to work on this

**Measure, don't inspect.** Nearly every real insight came from data contradicting the design: the gate was backwards, a livelock bumped one wall 715 times, the agent "ate" 225 times while starving beside food, a memory experiment's probe was circular, and a "46% error rate" was my own misconfiguration rather than a capability floor.

The **conscious stream** (`--stream`, or the viewer panel) is the best debugging instrument here — rendering it found four bugs that passing tests and healthy averages had hidden. Read what the agent says about itself.

When a hypothesis fails, **report it failed.** H4 is falsified and stays that way; the salvage was that learned per-entity persistence detects the losing predictor and collapses it to the baseline.

## Open threads

- **One seed per model** in the sweep — shows the floor is reachable, not that model ranking is stable. More seeds would firm this up.
- **~8–10% error rate** across all models, absorbed by the fallback path. The architecture's robustness is partly masking model failures; separating the two would be a better test.
- **Sleep is arithmetic, not distillation** — it counts entity frequencies. Using an LLM for genuine semantic consolidation is the obvious upgrade (`memory.Memory.sleep`).
- H4 could be revisited with lower sensor noise or entities whose intent persists across observation gaps.
