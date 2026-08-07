"""The P0 agent: a placeholder mind that acts at random.

It has no perception, no belief, and issues no LLM call — those are the whole
point of P1+ and deliberately absent here. Its only job is to exercise the
harness (tick loop, logging, replay) with a reproducible action stream. It
conforms to the shape a real mind will later fill: `act(world, rng) -> Action`.
"""

from __future__ import annotations

from .world import Action, World

# Fixed option order matters for reproducibility — rng.choice indexes this list.
_OPTIONS = [Action("move", d) for d in ("N", "S", "E", "W")] + [Action("wait")]


class RandomAgent:
    def act(self, world: World, agent_rng) -> Action:  # noqa: ARG002 (world unused in P0)
        return agent_rng.choice(_OPTIONS)
