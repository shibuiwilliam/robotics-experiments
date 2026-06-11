"""Build a SceneGraph from a MuJoCo world."""

from __future__ import annotations

import numpy as np

from mws.core.types import EntityKind, RelationType
from mws.sim.world import MuJoCoWorld
from mws.worldmodel.entity import EntityNode
from mws.worldmodel.relation import Relation
from mws.worldmodel.scene_graph import SceneGraph


def build_scene_graph_from_world(
    world: MuJoCoWorld,
    world_id: str = "default",
    near_threshold: float = 3.0,
) -> SceneGraph:
    """Create a SceneGraph from the bodies in a MuJoCo world.

    For each named body an :class:`EntityNode` (kind=OBJECT) is added.
    SPATIAL_NEAR relations are created between every pair of bodies whose
    Euclidean distance is within *near_threshold*.

    Args:
        world: The MuJoCo world to inspect.
        world_id: Identifier for the scene graph world.
        near_threshold: Maximum distance for a SPATIAL_NEAR relation.

    Returns:
        A populated :class:`SceneGraph`.
    """
    graph = SceneGraph(world_id=world_id)

    body_positions = world.get_body_positions()

    # Create entity nodes for each named body
    entities: list[tuple[str, np.ndarray]] = []
    for name, pos in body_positions.items():
        if not name:
            continue
        entity = EntityNode(
            entity_id=name,
            kind=EntityKind.OBJECT,
            name=name,
            position=(float(pos[0]), float(pos[1]), float(pos[2])),
        )
        graph.add_entity(entity)
        entities.append((name, pos))

    # Create SPATIAL_NEAR relations for pairs within threshold
    for i, (name_a, pos_a) in enumerate(entities):
        for j in range(i + 1, len(entities)):
            name_b, pos_b = entities[j]
            dist = float(np.linalg.norm(pos_a - pos_b))
            if dist <= near_threshold:
                weight = max(0.0, min(1.0, 1.0 - dist / near_threshold))
                relation = Relation(
                    source_id=name_a,
                    target_id=name_b,
                    relation_type=RelationType.SPATIAL_NEAR,
                    weight=weight,
                    timestamp=world.time,
                    properties={"distance": dist},
                )
                graph.add_relation(relation)

    return graph
