"""4D Scene Graph — the world model backbone of MWS.

Entities (objects, places, agents) live here with typed relations.
Atoms attach to entities, grounding heterogeneous data to the same real-world things.
"""

from __future__ import annotations

from mws.core.logging import get_logger
from mws.core.types import EntityKind, RelationType
from mws.worldmodel.entity import EntityNode
from mws.worldmodel.relation import Relation

logger = get_logger(__name__)


class SceneGraph:
    """4D scene graph: entities + relations + temporal snapshots.

    Uses NetworkX internally for graph operations.
    """

    def __init__(self, world_id: str = "default") -> None:
        self.world_id = world_id
        self._entities: dict[str, EntityNode] = {}
        self._relations: list[Relation] = []

    def add_entity(self, entity: EntityNode) -> None:
        """Add or update an entity in the graph."""
        self._entities[entity.entity_id] = entity

    def get_entity(self, entity_id: str) -> EntityNode | None:
        """Get entity by ID."""
        return self._entities.get(entity_id)

    def remove_entity(self, entity_id: str) -> None:
        """Remove entity and its relations."""
        self._entities.pop(entity_id, None)
        self._relations = [
            r for r in self._relations if r.source_id != entity_id and r.target_id != entity_id
        ]

    def add_relation(self, relation: Relation) -> None:
        """Add a relation between entities."""
        if relation.source_id not in self._entities:
            raise ValueError(f"Source entity {relation.source_id} not in graph")
        if relation.target_id not in self._entities:
            raise ValueError(f"Target entity {relation.target_id} not in graph")
        self._relations.append(relation)

    def get_relations(
        self,
        entity_id: str,
        relation_type: RelationType | None = None,
    ) -> list[Relation]:
        """Get relations involving an entity, optionally filtered by type."""
        results = []
        for r in self._relations:
            if (r.source_id == entity_id or r.target_id == entity_id) and (
                relation_type is None or r.relation_type == relation_type
            ):
                results.append(r)
        return results

    def entities_by_kind(self, kind: EntityKind) -> list[EntityNode]:
        """Get all entities of a given kind."""
        return [e for e in self._entities.values() if e.kind == kind]

    def attach_atom_to_entity(self, entity_id: str, atom_id: str) -> None:
        """Attach an atom to an entity."""
        entity = self._entities.get(entity_id)
        if entity is None:
            raise ValueError(f"Entity {entity_id} not in graph")
        entity.attach_atom(atom_id)

    def find_nearby_entities(
        self,
        position: tuple[float, float, float],
        radius: float,
    ) -> list[EntityNode]:
        """Find entities within a spatial radius (Euclidean)."""
        results = []
        for entity in self._entities.values():
            dx = entity.position[0] - position[0]
            dy = entity.position[1] - position[1]
            dz = entity.position[2] - position[2]
            dist = (dx * dx + dy * dy + dz * dz) ** 0.5
            if dist <= radius:
                results.append(entity)
        return results

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    @property
    def relation_count(self) -> int:
        return len(self._relations)

    def all_entities(self) -> list[EntityNode]:
        """Return all entities."""
        return list(self._entities.values())
