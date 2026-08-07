"""The Frame — the central interface (PLAN.md §4.1).

Everything hangs off this. The sensorium produces it, the worldmodel predicts
it, the mind consumes it. Two properties are load-bearing:

  * Entities are an egocentric POLAR LIST, not a 2D grid. Language models read
    character grids badly; (kind, bearing, range) tuples they handle well.
  * `age` and `conf` are visible to the agent. That is what makes "cup, last
    seen 400 ticks ago, low confidence" a representable thought — and it is the
    precondition for epistemic action (H3).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EntityView:
    id: str
    kind: str
    bearing: float          # degrees relative to heading, (-180, 180]
    range: float
    conf: float             # 1.0 when directly observed; decays when remembered
    age: int                # ticks since last actually seen (0 = right now)
    observed: bool          # True = sensed this tick, False = confabulated
    visits: float = 0.0     # how much time the agent has spent near it lately
    state: dict = field(default_factory=dict)
    # For animate entities, ranked alternatives instead of one collapsed point.
    hypotheses: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "kind": self.kind,
            "bearing": self.bearing,
            "range": self.range,
            "conf": round(self.conf, 3),
            "age": self.age,
            "observed": self.observed,
            "visits": round(self.visits, 2),
        }
        if self.state:
            d["state"] = self.state
        if self.hypotheses:
            d["hypotheses"] = self.hypotheses
        return d


@dataclass
class Frame:
    tick: int
    pose: tuple[float, float, float]     # believed (x, y, heading)
    pose_conf: float
    efference: dict | None               # copy of own motor command last tick
    drives: dict = field(default_factory=dict)
    valence: float = 0.0
    arousal: float = 0.0
    entities: list[EntityView] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    affordances: list[dict] = field(default_factory=list)
    budget: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tick": self.tick,
            "pose": [round(v, 3) for v in self.pose],
            "pose_conf": round(self.pose_conf, 3),
            "efference": self.efference,
            "drives": {k: round(v, 3) for k, v in sorted(self.drives.items())},
            "valence": round(self.valence, 3),
            "arousal": round(self.arousal, 3),
            "entities": [e.to_dict() for e in self.entities],
            "events": self.events,
            "affordances": self.affordances,
            "budget": self.budget,
        }
