"""Panda adapter: MuJoCo native state ↔ Canonical IR.

Converts between:
  - Native: dict with 'joint_positions' (7,), 'joint_velocities' (7,),
    'ee_position' (3,), 'ee_quaternion' (4,), 'time' (float)
  - IR: IRState with Phytes for each joint and end-effector

All in SI units (rad, m, s), world frame, z-up.
Covariance from a simple sensor noise model.
"""

from __future__ import annotations

import numpy as np
from pytransform3d import rotations as pr

from psl.contracts.core import FidelityContract
from psl.ir.core import IRState
from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3, make_se3
from psl.phyte.provenance import Provenance, ProvenanceEntry

# Panda joint limits (rad) — from Franka Emika spec
PANDA_JOINT_LIMITS_LOWER = np.array(
    [-2.8973, -1.7628, -2.8973, -3.0718, -2.8973, -0.0175, -2.8973]
)
PANDA_JOINT_LIMITS_UPPER = np.array([2.8973, 1.7628, 2.8973, -0.0698, 2.8973, 3.7525, 2.8973])
PANDA_VELOCITY_LIMITS = np.array([2.1750, 2.1750, 2.1750, 2.1750, 2.6100, 2.6100, 2.6100])

# Sensor noise model (standard deviations)
JOINT_POS_NOISE_STD = 1e-4  # rad
JOINT_VEL_NOISE_STD = 1e-3  # rad/s
EE_POS_NOISE_STD = 5e-4  # m
EE_ROT_NOISE_STD = 1e-3  # rad


class PandaAdapter:
    """Adapter for a 7-DOF Panda arm: native MuJoCo ↔ IR.

    Implements the Adapter protocol (to_ir / from_ir).
    All Phytes carry covariance from the sensor noise model
    and provenance tracking.

    Args:
        entity_id: Unique identifier for this robot instance.
    """

    def __init__(self, entity_id: str = "panda_0") -> None:
        self._entity_id = entity_id

    @property
    def entity_id(self) -> str:
        return self._entity_id

    @property
    def fidelity_contract(self) -> FidelityContract:
        """Contract for native ↔ IR translation."""
        return FidelityContract(
            adapter_id=f"panda_adapter:{self._entity_id}",
            preserved_fields=["value", "unit", "frame", "timestamp", "clock_domain"],
            lost_fields=[],
            uncertainty_delta=JOINT_POS_NOISE_STD,
            information_loss_estimate=0.01,
            notes="Near-lossless: sensor noise model adds small covariance. "
            "Joint positions in rad, velocities in rad/s, EE pose as SE(3).",
        )

    def to_ir(self, native_state: dict[str, object]) -> IRState:
        """Convert native MuJoCo state to canonical IR.

        Args:
            native_state: Dict with keys:
                'joint_positions': (7,) array in rad
                'joint_velocities': (7,) array in rad/s
                'ee_position': (3,) array in m
                'ee_quaternion': (4,) array wxyz
                'time': float (sim seconds)

        Returns:
            IRState with Phytes in world frame, SI units.
        """
        jpos = np.asarray(native_state["joint_positions"], dtype=np.float64)
        jvel = np.asarray(native_state["joint_velocities"], dtype=np.float64)
        ee_pos = np.asarray(native_state["ee_position"], dtype=np.float64)
        ee_quat = np.asarray(native_state["ee_quaternion"], dtype=np.float64)
        sim_time = float(native_state["time"])  # type: ignore[arg-type]

        base_prov = Provenance(
            chain=[
                ProvenanceEntry(
                    source=f"mujoco_sensor:{self._entity_id}",
                    operation="read_sensor",
                    timestamp=sim_time,
                )
            ],
            confidence=0.99,
        )

        phytes: dict[str, Phyte] = {}

        # Joint positions
        for i in range(7):
            cov = np.array([[JOINT_POS_NOISE_STD**2]])
            phytes[f"joint_{i}"] = Phyte(
                semantic_id=f"joint_position_{i}",
                frame="world",
                pose=identity_se3(),
                timestamp=sim_time,
                clock_domain="sim",
                unit="rad",
                value=np.array([jpos[i]]),
                covariance=cov,
                provenance=base_prov,
            )

        # Joint velocities
        for i in range(7):
            cov = np.array([[JOINT_VEL_NOISE_STD**2]])
            phytes[f"joint_vel_{i}"] = Phyte(
                semantic_id=f"joint_velocity_{i}",
                frame="world",
                pose=identity_se3(),
                timestamp=sim_time,
                clock_domain="sim",
                unit="rad/s",
                value=np.array([jvel[i]]),
                covariance=cov,
                provenance=base_prov,
            )

        # End-effector pose as SE(3)
        R = pr.matrix_from_quaternion(ee_quat)
        ee_se3 = make_se3(R, ee_pos)
        phytes["ee_pose"] = Phyte(
            semantic_id="end_effector_pose",
            frame="world",
            pose=ee_se3,
            timestamp=sim_time,
            clock_domain="sim",
            unit="m",
            value=np.concatenate([ee_pos, ee_quat]),
            covariance=np.eye(7) * EE_POS_NOISE_STD**2,
            provenance=base_prov,
        )

        return IRState(
            entity_id=self._entity_id,
            phytes=phytes,
            timestamp=sim_time,
            clock_domain="sim",
        )

    def from_ir(self, ir_state: IRState) -> dict[str, object]:
        """Convert canonical IR back to native MuJoCo state dict.

        Args:
            ir_state: IRState with Phytes.

        Returns:
            Dict with 'joint_positions', 'joint_velocities',
            'ee_position', 'ee_quaternion', 'time'.
        """
        jpos = np.zeros(7)
        jvel = np.zeros(7)

        for i in range(7):
            key = f"joint_{i}"
            if key in ir_state.phytes:
                jpos[i] = float(ir_state.phytes[key].value[0])

            vel_key = f"joint_vel_{i}"
            if vel_key in ir_state.phytes:
                jvel[i] = float(ir_state.phytes[vel_key].value[0])

        ee_pos = np.zeros(3)
        ee_quat = np.array([1.0, 0.0, 0.0, 0.0])

        if "ee_pose" in ir_state.phytes:
            ee_val = ir_state.phytes["ee_pose"].value
            ee_pos = ee_val[:3].copy()
            ee_quat = ee_val[3:7].copy()

        return {
            "joint_positions": jpos,
            "joint_velocities": jvel,
            "ee_position": ee_pos,
            "ee_quaternion": ee_quat,
            "time": ir_state.timestamp,
        }
