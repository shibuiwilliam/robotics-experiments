"""Tests for graph store."""

from mws.core.types import EntityKind, RelationType
from mws.storage.graph import GraphStore
from mws.worldmodel.entity import EntityNode
from mws.worldmodel.relation import Relation


def test_add_entity_and_relation() -> None:
    gs = GraphStore()
    gs.add_entity(EntityNode(entity_id="a", kind=EntityKind.OBJECT))
    gs.add_entity(EntityNode(entity_id="b", kind=EntityKind.PLACE))
    gs.add_relation(
        Relation(source_id="a", target_id="b", relation_type=RelationType.SPATIAL_CONTAINS)
    )
    assert gs.node_count == 2
    assert gs.edge_count == 1
    assert "b" in gs.get_neighbors("a")


def test_has_entity() -> None:
    gs = GraphStore()
    gs.add_entity(EntityNode(entity_id="x", kind=EntityKind.AGENT))
    assert gs.has_entity("x")
    assert not gs.has_entity("y")
