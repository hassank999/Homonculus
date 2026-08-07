"""World state -> egocentric observation.

This is the ONLY module that touches ground truth on the agent's behalf, and it
deliberately hands back nothing allocentric: observations are (bearing, range)
relative to the agent's TRUE pose, quantized by distance band. The worldmodel
then has to reconstruct world coordinates using its BELIEVED pose — and that
gap is where belief error comes from.

Sensing is limited by radius and by line of sight, so walls create the
unobserved regions that make confabulation matter.
"""

from __future__ import annotations

from dataclasses import dataclass

from .geometry import abs_bearing, dist, quantize, rel_bearing, visible

SENSE_RADIUS = 8.0


@dataclass(frozen=True)
class Observation:
    id: str
    kind: str
    bearing: float          # relative to true heading, quantized
    range: float            # quantized
    state: dict


def observe(world, sense_radius: float = SENSE_RADIUS) -> list[Observation]:
    """Everything the agent's senses deliver this tick, sorted by id."""
    agent = world.entities["agent"]
    origin = (agent.x, agent.y)
    out: list[Observation] = []
    for eid in sorted(world.entities):
        if eid == "agent":
            continue
        e = world.entities[eid]
        r = dist(origin, (e.x, e.y))
        if r > sense_radius:
            continue
        if not visible(origin, (e.x, e.y), world.walls):
            continue
        qr, qb = quantize(r, rel_bearing(abs_bearing(origin, (e.x, e.y)), agent.heading))
        out.append(Observation(eid, e.kind, qb, qr, dict(e.state)))
    return out


def observed_events(world, tick_event: dict) -> list[dict]:
    """Discrete exteroceptive events. Blocked moves are the important one: the
    agent bumps something it may not have seen, which is both a real percept and
    the main source of pose drift."""
    evs: list[dict] = []
    if tick_event.get("blocked"):
        evs.append({"kind": "bump", "dir": tick_event["action"].get("dir")})
    return evs
