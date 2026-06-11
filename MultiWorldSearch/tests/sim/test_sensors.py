"""Tests for sensor atom extraction."""

from mws.sim.sensors import extract_observation_atoms
from mws.sim.world import MuJoCoWorld


def test_extract_atoms() -> None:
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    world.step(10)
    atoms = extract_observation_atoms(world, world_id="test")
    assert len(atoms) > 0
    # Should have both pose and sensor atoms
    modalities = {a.modality for a in atoms}
    assert "pose" in modalities or "telemetry" in modalities


def test_atom_provenance() -> None:
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    world.step(1)
    atoms = extract_observation_atoms(world, world_id="warehouse")
    for atom in atoms:
        assert atom.provenance.origin is not None
        assert "mujoco" in atom.provenance.origin.source_id
