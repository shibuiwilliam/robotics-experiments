"""Skill demonstration atoms — schema and injection."""

from __future__ import annotations

import numpy as np

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality


def create_skill_demo_atom(
    skill_name: str,
    equipment_type: str,
    trajectory: list[list[float]] | None = None,
    seed: int = 0,
) -> ExperienceAtom:
    """Create a skill demonstration atom for future retrieval by VLA.

    This represents a successful past execution that can be retrieved
    as in-context example for similar tasks.
    """
    rng = np.random.default_rng(seed)

    if trajectory is None:
        # Generate mock trajectory (6-DOF, 10 steps)
        trajectory = rng.standard_normal((10, 6)).tolist()

    assert trajectory is not None  # for type narrowing
    coord = SpatiotemporalCoord(
        x=2.0,
        y=0.0,
        z=0.5,
        timestamp=1699910000.0,
    )

    atom = ExperienceAtom(
        modality=Modality.SKILL_DEMO,
        coord=coord,
        text_summary=(
            f"Skill demonstration: {skill_name} on {equipment_type}. "
            f"Trajectory with {len(trajectory)} steps."
        ),
        tags=["skill_demo", skill_name, equipment_type],
        structured_fields={
            "skill_name": skill_name,
            "equipment_type": equipment_type,
            "trajectory_length": len(trajectory),
        },
        payload={"trajectory": trajectory},
    )
    atom.provenance.add("vla_recording", "agent", "created")
    return atom
