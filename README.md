# HOMUNCULUS

An embodied agent that maintains a *belief* about a world rather than reading it directly.

Every tick, a cheap loop advances a small simulated world. The agent holds its own estimate of that world and emits a prediction of what it expects to perceive next. The gap between prediction and observation — **surprise** — does four jobs from one signal: it drives attention, schedules whether an LLM call happens at all, decides what gets written to long-term memory, and corrects the agent's model of how the world moves.

Unobserved parts of the world aren't simulated in the agent's head. They're timestamped and **confabulated lazily on read**, with drift as a deterministic function of elapsed time and entity class. Memory and imagination turn out to be the same generative pass, differing only in whether observation is clamping it.

**Thesis:** prediction error is sufficient as the central currency of an embodied agent, and gating cognition on it makes the whole thing affordable.

## Status

Planning complete, entering execution. See [`PLAN.md`](PLAN.md) for the full design, falsifiable claims (H1–H4), phased build, and cost model. Running progress goes in [`PROJECT_UPDATES.md`](PROJECT_UPDATES.md).

## Stack

- Python 3.14, numpy
- [Together AI](https://together.ai) serverless inference, open-source models, behind a model-agnostic `LLMProvider` interface (model choice is an experimental variable, not a constant)
- LLM client layer and event-log/replay engine vendored from [Vivarium](../Vivarium) (see `PLAN.md` §10)

## Layout (planned)

```
homunculus/
  world/        tick-discrete gridworld
  sensorium/    world state -> egocentric observation
  worldmodel/   the agent's belief; path integration, lazy confabulation
  surprise/     predicted vs actual -> the central signal
  soma/         drives, valence, arousal
  memory/       working / episodic / semantic / procedural + sleep pass
  mind/         the LLM call, behind LLMProvider
  motor/        durative action commitments with controllers
  loop/         tick scheduler + concurrency governor
  scope/        per-tick log, seeded replay, timeline viewer
```
