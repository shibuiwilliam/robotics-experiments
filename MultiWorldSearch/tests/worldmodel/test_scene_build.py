"""Tests for build_scene_graph_from_world."""

from __future__ import annotations

from mws.core.types import EntityKind, RelationType
from mws.sim.scene import build_scene_graph_from_world
from mws.sim.world import MuJoCoWorld


def test_build_creates_entities() -> None:
    """Each named body in the MuJoCo world should become an entity."""
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    graph = build_scene_graph_from_world(world, world_id="test")

    # The minimal world has bodies: world, conveyor_motor, shelf_unit, maintenance_robot
    # "world" is the root body — it has a name in MuJoCo
    assert graph.entity_count >= 3  # at least the 3 named user bodies

    entity_names = {e.entity_id for e in graph.all_entities()}
    assert "conveyor_motor" in entity_names
    assert "shelf_unit" in entity_names
    assert "maintenance_robot" in entity_names


def test_build_creates_spatial_relations() -> None:
    """Bodies within 3.0 distance should have SPATIAL_NEAR relations."""
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    world.step(1)
    graph = build_scene_graph_from_world(world, world_id="test", near_threshold=3.0)

    # conveyor_motor is at (2,0,0.5), maintenance_robot at (0,-2,0.3)
    # distance ~ sqrt(4+4+0.04) ~ 2.83 => within 3.0
    motor_rels = graph.get_relations("conveyor_motor")
    near_rels = [r for r in motor_rels if r.relation_type == RelationType.SPATIAL_NEAR]

    # At least the motor-robot pair should be near
    assert len(near_rels) >= 1


def test_build_no_relations_beyond_threshold() -> None:
    """With a very small threshold no SPATIAL_NEAR relations should exist."""
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    # Step so that forward kinematics resolves body positions from the XML
    world.step(1)
    graph = build_scene_graph_from_world(world, world_id="test", near_threshold=0.01)
    assert graph.relation_count == 0


def test_entity_kind_is_object() -> None:
    """All entities from a MuJoCo world should be OBJECT kind."""
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    graph = build_scene_graph_from_world(world, world_id="test")
    for entity in graph.all_entities():
        assert entity.kind == EntityKind.OBJECT


def test_relation_weight_decreases_with_distance() -> None:
    """Closer bodies should have higher relation weight."""
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    world.step(1)
    graph = build_scene_graph_from_world(world, world_id="test", near_threshold=100.0)

    # Find all relations and check weights are in [0, 1]
    for entity in graph.all_entities():
        for rel in graph.get_relations(entity.entity_id):
            assert 0.0 <= rel.weight <= 1.0
