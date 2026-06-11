"""Graph store adapter using NetworkX for scene graph persistence."""

from __future__ import annotations

import networkx as nx

from mws.core.logging import get_logger
from mws.worldmodel.entity import EntityNode
from mws.worldmodel.relation import Relation

logger = get_logger(__name__)


class GraphStore:
    """NetworkX-backed graph store for the scene graph."""

    def __init__(self) -> None:
        self._graph = nx.DiGraph()

    def add_entity(self, entity: EntityNode) -> None:
        """Add or update an entity node."""
        self._graph.add_node(
            entity.entity_id,
            kind=entity.kind,
            name=entity.name,
            position=entity.position,
            properties=entity.properties,
            atom_ids=list(entity.atom_ids),
        )

    def add_relation(self, relation: Relation) -> None:
        """Add a directed edge."""
        self._graph.add_edge(
            relation.source_id,
            relation.target_id,
            relation_type=relation.relation_type,
            weight=relation.weight,
            timestamp=relation.timestamp,
            properties=relation.properties,
        )

    def get_neighbors(self, entity_id: str) -> list[str]:
        """Get neighbor entity IDs."""
        if entity_id not in self._graph:
            return []
        return list(self._graph.successors(entity_id)) + list(self._graph.predecessors(entity_id))

    def has_entity(self, entity_id: str) -> bool:
        return entity_id in self._graph

    @property
    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._graph.number_of_edges()
