"""Tests for 4D scene graph."""

import pytest

from mws.core.types import EntityKind, RelationType
from mws.worldmodel.entity import EntityNode
from mws.worldmodel.relation import Relation
from mws.worldmodel.scene_graph import SceneGraph


def test_add_and_get_entity() -> None:
    sg = SceneGraph()
    entity = EntityNode(entity_id="conv-01", kind=EntityKind.OBJECT, name="Conveyor Motor")
    sg.add_entity(entity)
    assert sg.entity_count == 1
    assert sg.get_entity("conv-01") is not None
    assert sg.get_entity("nonexistent") is None


def test_add_relation() -> None:
    sg = SceneGraph()
    sg.add_entity(EntityNode(entity_id="zone-a", kind=EntityKind.ZONE, name="Zone A"))
    sg.add_entity(
        EntityNode(entity_id="conv-01", kind=EntityKind.OBJECT, name="Conveyor", position=(1, 2, 0))
    )
    rel = Relation(
        source_id="zone-a",
        target_id="conv-01",
        relation_type=RelationType.SPATIAL_CONTAINS,
    )
    sg.add_relation(rel)
    assert sg.relation_count == 1
    rels = sg.get_relations("conv-01")
    assert len(rels) == 1
    assert rels[0].relation_type == RelationType.SPATIAL_CONTAINS


def test_relation_missing_entity() -> None:
    sg = SceneGraph()
    sg.add_entity(EntityNode(entity_id="a", kind=EntityKind.OBJECT))
    with pytest.raises(ValueError, match="not in graph"):
        sg.add_relation(
            Relation(source_id="a", target_id="missing", relation_type=RelationType.CAUSAL)
        )


def test_attach_atom() -> None:
    sg = SceneGraph()
    sg.add_entity(EntityNode(entity_id="motor-01", kind=EntityKind.OBJECT))
    sg.attach_atom_to_entity("motor-01", "atom-abc")
    entity = sg.get_entity("motor-01")
    assert entity is not None
    assert "atom-abc" in entity.atom_ids


def test_find_nearby() -> None:
    sg = SceneGraph()
    sg.add_entity(EntityNode(entity_id="a", kind=EntityKind.OBJECT, position=(0, 0, 0)))
    sg.add_entity(EntityNode(entity_id="b", kind=EntityKind.OBJECT, position=(1, 0, 0)))
    sg.add_entity(EntityNode(entity_id="c", kind=EntityKind.OBJECT, position=(10, 10, 10)))
    nearby = sg.find_nearby_entities((0, 0, 0), radius=2.0)
    ids = {e.entity_id for e in nearby}
    assert "a" in ids
    assert "b" in ids
    assert "c" not in ids


def test_entities_by_kind() -> None:
    sg = SceneGraph()
    sg.add_entity(EntityNode(entity_id="r1", kind=EntityKind.AGENT, name="Robot 1"))
    sg.add_entity(EntityNode(entity_id="obj1", kind=EntityKind.OBJECT, name="Motor"))
    agents = sg.entities_by_kind(EntityKind.AGENT)
    assert len(agents) == 1
    assert agents[0].entity_id == "r1"
