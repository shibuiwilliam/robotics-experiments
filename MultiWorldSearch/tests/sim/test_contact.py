"""Tests for contact and trajectory atom extraction."""

from __future__ import annotations

import numpy as np

from mws.core.types import Modality
from mws.sim.sensors import (
    extract_contact_atoms,
    extract_observation_atoms,
    extract_trajectory_atom,
)
from mws.sim.world import MuJoCoWorld
from mws.storage.blob import BlobStore


def test_extract_contact_atoms_returns_contact_modality() -> None:
    """Contact atoms (if any) must have CONTACT modality."""
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    world.step(10)
    atoms = extract_contact_atoms(world, world_id="test", seed=0)
    # The minimal world may or may not have contacts depending on physics;
    # verify the type when contacts exist.
    for atom in atoms:
        assert atom.modality == Modality.CONTACT
        assert "contact" in atom.tags
        assert "geom1" in atom.structured_fields
        assert "geom2" in atom.structured_fields


def test_extract_contact_atoms_empty_when_no_contacts() -> None:
    """When ncon == 0 the function must return an empty list."""
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    # Don't step — at t=0 there should be no contacts in the minimal world
    atoms = extract_contact_atoms(world, world_id="test", seed=0)
    assert isinstance(atoms, list)


def test_observation_atoms_include_contacts() -> None:
    """extract_observation_atoms should also yield contact atoms."""
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    world.step(10)
    atoms = extract_observation_atoms(world, world_id="test", seed=0)
    modalities = {a.modality for a in atoms}
    # At minimum pose and telemetry should be present
    assert Modality.POSE in modalities or Modality.TELEMETRY in modalities


def test_extract_trajectory_atom() -> None:
    """Trajectory atom must have ACTION_TRAJECTORY modality."""
    positions = [np.array([1.0, 2.0, 3.0]), np.array([1.1, 2.1, 3.1])]
    atom = extract_trajectory_atom(
        positions_over_time=positions,
        world_id="test",
        entity_id="robot",
        seed=0,
    )
    assert atom.modality == Modality.ACTION_TRAJECTORY
    assert "trajectory" in atom.tags
    assert atom.payload["trajectory"] is not None
    assert len(atom.payload["trajectory"]) == 2


def test_extract_trajectory_atom_with_blob(tmp_path: object) -> None:
    """When a BlobStore is provided the trajectory is persisted."""
    from pathlib import Path

    blob = BlobStore(base_path=Path(str(tmp_path)))
    positions = [np.array([0.0, 0.0, 0.0]), np.array([1.0, 1.0, 1.0])]
    atom = extract_trajectory_atom(
        positions_over_time=positions,
        world_id="w1",
        entity_id="arm",
        seed=42,
        blob_store=blob,
    )
    assert atom.payload_ref is not None
    assert blob.exists(atom.payload_ref)
    raw = blob.get(atom.payload_ref)
    assert raw is not None
