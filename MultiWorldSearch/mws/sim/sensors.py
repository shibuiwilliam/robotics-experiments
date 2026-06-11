"""Sensor data extraction and atom conversion."""

from __future__ import annotations

import pickle

import numpy as np

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality
from mws.sim.world import MuJoCoWorld
from mws.storage.blob import BlobStore


def extract_contact_atoms(
    world: MuJoCoWorld,
    world_id: str = "default",
    seed: int = 0,
) -> list[ExperienceAtom]:
    """Extract contact information from MuJoCo as CONTACT modality atoms.

    Reads ``world.data.ncon`` and ``world.data.contact`` to produce one atom
    per active contact pair, recording geom IDs and contact force.
    Returns an empty list when there are no contacts.
    """
    ncon: int = world.data.ncon
    if ncon == 0:
        return []

    atoms: list[ExperienceAtom] = []
    timestamp = world.time

    for i in range(ncon):
        contact = world.data.contact[i]
        geom1 = int(contact.geom1)
        geom2 = int(contact.geom2)
        pos = contact.pos  # 3-element contact position

        coord = SpatiotemporalCoord(
            x=float(pos[0]),
            y=float(pos[1]),
            z=float(pos[2]),
            timestamp=timestamp,
            world_id=world_id,
        )

        # Contact force magnitude (first 3 elements of contact frame force)
        force = float(np.linalg.norm(contact.frame[:3]))

        atom = ExperienceAtom(
            modality=Modality.CONTACT,
            coord=coord,
            text_summary=(
                f"Contact between geom {geom1} and geom {geom2} "
                f"at ({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f}) force={force:.4f}"
            ),
            tags=["sim", "contact", f"geom{geom1}", f"geom{geom2}"],
            structured_fields={
                "geom1": geom1,
                "geom2": geom2,
                "force": force,
            },
            payload={
                "geom1": geom1,
                "geom2": geom2,
                "position": pos.tolist(),
                "force": force,
            },
        )
        atom.provenance.add(f"mujoco:{world_id}:contact:{i}", "sensor", "created")
        atoms.append(atom)

    return atoms


def extract_trajectory_atom(
    positions_over_time: list[np.ndarray],
    world_id: str = "default",
    entity_id: str = "agent",
    seed: int = 0,
    blob_store: BlobStore | None = None,
) -> ExperienceAtom:
    """Create an ACTION_TRAJECTORY atom from recorded joint positions.

    Args:
        positions_over_time: List of joint-position arrays recorded over
            multiple simulation steps.
        world_id: World identifier.
        entity_id: Entity (e.g. robot) this trajectory belongs to.
        seed: Random seed for reproducibility.
        blob_store: Optional BlobStore for persisting the raw trajectory.

    Returns:
        A single ``ExperienceAtom`` with modality ACTION_TRAJECTORY.
    """
    if not positions_over_time:
        raise ValueError("positions_over_time must be non-empty")

    # Use the midpoint of the first recorded position for spatial coord
    first_pos = positions_over_time[0]
    coord = SpatiotemporalCoord(
        x=float(first_pos[0]) if len(first_pos) > 0 else 0.0,
        y=float(first_pos[1]) if len(first_pos) > 1 else 0.0,
        z=float(first_pos[2]) if len(first_pos) > 2 else 0.0,
        timestamp=0.0,
        world_id=world_id,
    )

    trajectory_list = [p.tolist() for p in positions_over_time]

    atom = ExperienceAtom(
        modality=Modality.ACTION_TRAJECTORY,
        coord=coord,
        text_summary=(f"Trajectory for '{entity_id}' with {len(positions_over_time)} steps"),
        tags=["sim", "trajectory", entity_id],
        entity_id=entity_id,
        structured_fields={"entity_id": entity_id, "n_steps": len(positions_over_time)},
        payload={"trajectory": trajectory_list},
    )

    # Store raw trajectory bytes in the BlobStore
    if blob_store is not None:
        blob_key = f"trajectory/{world_id}/{entity_id}/{seed}"
        raw_bytes = pickle.dumps(trajectory_list)
        blob_store.put(blob_key, raw_bytes, metadata={"entity_id": entity_id, "seed": seed})
        atom.payload_ref = blob_key

    atom.provenance.add(f"mujoco:{world_id}:trajectory", "sensor", "created")
    return atom


def extract_observation_atoms(
    world: MuJoCoWorld,
    world_id: str = "default",
    seed: int = 0,
) -> list[ExperienceAtom]:
    """Extract current observations from a MuJoCo world as atoms.

    Creates atoms for:
    - Body positions (pose modality)
    - Sensor readings (telemetry modality)
    - Contact pairs (contact modality)
    """
    atoms: list[ExperienceAtom] = []
    timestamp = world.time

    # Body position atoms
    for name, pos in world.get_body_positions().items():
        if not name:  # skip unnamed bodies
            continue
        coord = SpatiotemporalCoord(
            x=float(pos[0]),
            y=float(pos[1]),
            z=float(pos[2]),
            timestamp=timestamp,
            world_id=world_id,
        )
        atom = ExperienceAtom(
            modality=Modality.POSE,
            coord=coord,
            text_summary=f"Body '{name}' at position ({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})",
            tags=["sim", "pose", name],
            entity_id=name,
            structured_fields={"body_name": name},
            payload={"position": pos.tolist()},
        )
        atom.provenance.add(f"mujoco:{world_id}", "sensor", "created")
        atoms.append(atom)

    # Sensor data atoms
    for sensor_name, values in world.get_sensor_data().items():
        # Use the body position of the first body as a rough location
        body_positions = world.get_body_positions()
        first_body = next(iter(body_positions.values()), np.zeros(3))
        coord = SpatiotemporalCoord(
            x=float(first_body[0]),
            y=float(first_body[1]),
            z=float(first_body[2]),
            timestamp=timestamp,
            world_id=world_id,
        )
        atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=coord,
            text_summary=f"Sensor '{sensor_name}' reading: {values.tolist()}",
            tags=["sim", "sensor", sensor_name],
            structured_fields={"sensor_name": sensor_name, "value": float(values[0])},
            payload={"values": values.tolist()},
        )
        atom.provenance.add(f"mujoco:{world_id}:{sensor_name}", "sensor", "created")
        atoms.append(atom)

    # Contact atoms
    contact_atoms = extract_contact_atoms(world, world_id=world_id, seed=seed)
    atoms.extend(contact_atoms)

    return atoms
