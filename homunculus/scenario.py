"""Starting layouts.

Initial positions are fixed rather than seeded, so the scenario itself is stable
across runs and only behaviour is stochastic. Landmarks are the fixed points the
agent re-localizes against; the interior walls exist to create genuinely
unobserved regions, which is what makes confabulation load-bearing rather than
decorative.
"""

from __future__ import annotations

from .world import Entity, World

W = H = 24


def _room_walls() -> set[tuple[int, int]]:
    walls: set[tuple[int, int]] = set()
    for x in range(W):
        walls.add((x, 0))
        walls.add((x, H - 1))
    for y in range(H):
        walls.add((0, y))
        walls.add((W - 1, y))
    # Interior partition with two doorways -> occlusion and blocked moves.
    for y in range(1, H - 1):
        if y not in (7, 16):
            walls.add((12, y))
    for x in range(1, 12):
        if x not in (4, 9):
            walls.add((x, 12))
    return walls


def build(name: str = "apartment") -> World:
    if name != "apartment":
        raise ValueError(f"unknown scenario: {name!r}")
    walls = _room_walls()
    entities = [
        Entity("agent", "agent", 6, 6, heading=0.0),
        # Landmarks: static, uniquely identifiable, known a priori.
        Entity("lm_corner", "landmark", 2, 2),
        Entity("lm_pillar", "landmark", 9, 9),
        Entity("lm_alcove", "landmark", 20, 4),
        Entity("lm_hearth", "landmark", 18, 20),
        # Resources.
        Entity("food_a", "food", 4, 18, state={"available": True}),
        Entity("food_b", "food", 20, 9, state={"available": True}),
        Entity("warmth_a", "warmth", 18, 20),
        # Movable clutter.
        Entity("cup", "item", 8, 4),
        Entity("book", "item", 15, 15),
        # Animate — wanderers (intent does not persist).
        Entity("c00", "critter", 3, 10, goal=(10, 3)),
        Entity("c01", "critter", 18, 6, goal=(20, 18)),
        Entity("c02", "critter", 16, 19, goal=(14, 5)),
        # Animate — residents with routines (intent persists across occlusion).
        Entity("r00", "resident", 4, 4,
               state={"anchors": [[4, 4], [9, 18]], "target": 1, "dwell": 0}),
        Entity("r01", "resident", 20, 18,
               state={"anchors": [[20, 18], [16, 3]], "target": 1, "dwell": 0}),
    ]
    return World(W, H, walls, entities)


def landmarks(world: World) -> dict[str, tuple[int, int]]:
    return {
        e.id: (e.x, e.y)
        for e in world.entities.values()
        if e.kind == "landmark"
    }
