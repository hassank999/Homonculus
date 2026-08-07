# HOMUNCULUS

An embodied agent that maintains a *belief* about a world rather than reading it directly.

Every tick, a cheap loop advances a small simulated world. The agent holds its own estimate of that world and predicts what it expects to perceive next. The gap between prediction and observation — **surprise** — does four jobs from one signal: it drives attention, decides what gets written to long-term memory, corrects the agent's model of how the world moves, and gates whether an LLM call happens at all.

Unobserved parts of the world aren't simulated in the agent's head. They're timestamped and **confabulated lazily on read**, with drift as a deterministic function of elapsed time and entity class. Memory and imagination turn out to be the same generative pass, differing only in whether observation is clamping it.

**Thesis:** prediction error is sufficient as the central currency of an embodied agent, and gating cognition on it makes the whole thing affordable.

## Status: first light

All six phases built and measured. 50 tests green. Every hypothesis was tested against a baseline that could have falsified it, and one did.

| | Claim | Result |
|---|---|---|
| **H1** | Gating cuts LLM calls ≥50× without degrading behaviour | **Supported** — 60.9× fewer calls, behaviour **+14.5% better** |
| **H2** | Surprise-weighted consolidation beats recency and random | **Supported** — recall 0.197 vs 0.033 vs 0.010 |
| **H3** | Legible uncertainty produces epistemic action unprompted | **Supported** — 355 look-to-check actions, 34% refresh rate |
| **H4** | Intent-rollout predicts animate entities better than diffusion | **Falsified** — −2%, across four kinds of moving entity |

Two findings worth more than the checkmarks:

- **Thinking every tick is actively harmful.** It scored *worst* of all conditions. The agent reconsiders constantly and never finishes anything. More cognition is not better cognition.
- **I built the surprise gate backwards first.** Using surprise as an *interrupt* — abort what you're doing to think — was 7× more calls and 30% worse behaviour, monotonically at every threshold. The correct reading is the opposite: surprise should **remove** calls. At a decision point, if nothing surprising happened, repeat the standing plan without consulting the mind at all.

See [`PROJECT_UPDATES.md`](PROJECT_UPDATES.md) for the full arc, including the bugs each phase surfaced, and [`PLAN.md`](PLAN.md) for the design.

## Quick start

```bash
pip install -e ".[dev]"
python -m homunculus run --ticks 3000 --mind mock --view
```

That runs the full stack offline — no API key, no network, no spend — and writes a self-contained `runs/latest/replay.html` you can open and scrub. The viewer shows the agent's **true position against its believed position**, which is the whole story in one picture.

Reproduce the hypotheses:

```bash
python -m homunculus experiment all
```

Against real models on Together:

```bash
export TOGETHER_API_KEY=...
python -m homunculus run --ticks 2000 --mind together --model primary --view
```

## Architecture

```
world/      tick-discrete gridworld; deliberately dumb, it's a fixture
sensorium/  world state -> egocentric polar observation, with occlusion
worldmodel/ the agent's BELIEF: path integration, landmark re-localization,
            bump-based localization, lazy confabulation, loop closure
dynamics/   per-class drift; learned per-entity persistence
surprise/   predicted vs actual, NORMALIZED by what you already knew
soma/       homeostatic drives; valence and arousal are computed, not injected
motor/      durative commitments with controllers, A*, collision memory
mind/       the LLM call, behind a model-agnostic provider
gate/       when is it worth thinking? adaptive threshold, habitual action
memory/     working / episodic / semantic / procedural + sleep consolidation
multi/      two minds in one world; speech as an action
scope/      per-tick log, seeded replay, HTML timeline viewer
```

### The two ideas that carry the design

**Normalized surprise.** Raw error is not surprise. Being wrong about something you haven't seen in 10,000 ticks is *expected* and must not earn a memory write. Surprise is error relative to the uncertainty you already had — and expected error combines three independent sources: *it moved*, *I moved wrongly*, and *my senses are coarse*. Omitting the last two made static entities look 9× more surprising than they were, because the agent was blaming the world for its own error.

**Lazy confabulation.** The agent never stores where things are, only where they *were* and when. The "where is it now" answer is manufactured on demand and then believed. Because it's a pure function of `(record, elapsed, class)`, drift is auditable and time-travelable — you can ask what the agent would have believed at tick 4000 without running to tick 4000.

## Stack

Python 3.11+, numpy. [Together AI](https://together.ai) serverless inference behind a model-agnostic `LLMProvider` — model choice is an experimental variable, not a constant. A deterministic `MockProvider` makes the entire loop testable offline. LLM client patterns and the paced-replay approach draw on [Vivarium](../Vivarium) (see `PLAN.md` §10); seeded determinism, the tick scheduler, memory, and structured output are built here.

## Tests

```bash
python -m pytest -q
```

Several tests are regressions for bugs that cost real debugging time and each names the failure mode it guards — a livelock that bumped one wall 715 times, an agent that "ate" 225 times while starving beside food, an accumulator that saturated and fired every tick, and a memory experiment whose probe selection was circular.
