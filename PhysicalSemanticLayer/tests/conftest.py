"""Shared test fixtures — seed management, sim setup, adapter creation."""

from __future__ import annotations

import numpy as np
import pytest

from psl.adapters.robots.panda.adapter import PandaAdapter
from psl.safety.gate import JointLimits, PhysicsConsistencyGate
from sim.wrapper import MuJoCoSim

SEED = 42


@pytest.fixture
def rng() -> np.random.Generator:
    """Seeded random generator for reproducibility."""
    return np.random.default_rng(SEED)


@pytest.fixture
def sim() -> MuJoCoSim:
    """Fresh MuJoCo simulation."""
    return MuJoCoSim(seed=SEED)


@pytest.fixture
def adapter() -> PandaAdapter:
    """Panda adapter instance."""
    return PandaAdapter(entity_id="panda_test")


@pytest.fixture
def sim_stepped(sim: MuJoCoSim) -> MuJoCoSim:
    """Simulation after 100 steps (non-zero state)."""
    sim.step(100)
    return sim


@pytest.fixture
def native_state(sim_stepped: MuJoCoSim) -> dict[str, object]:
    """Native state from a stepped simulation."""
    jpos = sim_stepped.get_joint_positions()
    jvel = sim_stepped.get_joint_velocities()
    ee_pos, ee_quat = sim_stepped.get_ee_pose()
    return {
        "joint_positions": jpos,
        "joint_velocities": jvel,
        "ee_position": ee_pos,
        "ee_quaternion": ee_quat,
        "time": sim_stepped.time,
    }


@pytest.fixture
def safety_gate(sim: MuJoCoSim) -> PhysicsConsistencyGate:
    """Physics consistency gate with Panda joint limits."""
    lower, upper = sim.get_joint_limits()
    limits = JointLimits(
        position_lower=lower,
        position_upper=upper,
        velocity_max=np.array([2.175, 2.175, 2.175, 2.175, 2.61, 2.61, 2.61]),
    )
    return PhysicsConsistencyGate(joint_limits=limits)
