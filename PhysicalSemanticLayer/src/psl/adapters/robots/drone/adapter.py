"""Drone adapter: ENU native state ↔ Canonical IR.

Converts between:
  - Native: dict with 'position_enu' (3,) in ENU frame (m),
    'orientation_quat_hamilton' (4,) wxyz, 'linear_velocity_enu' (3,) m/s,
    'angular_velocity_body' (3,) rad/s, 'time' (float)
  - IR: IRState with Phytes for base_pose and velocities

Intentionally heterogeneous from Panda (6-DOF free body vs 7-DOF arm,
ENU vs z-up world frame, body-frame angular velocity).
"""

from __future__ import annotations

import numpy as np
from pytransform3d import rotations as pr

from psl.contracts.core import FidelityContract
from psl.ir.core import IRState
from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3, make_se3
from psl.phyte.provenance import Provenance, ProvenanceEntry

# Sensor noise model
POSITION_NOISE_STD = 1e-3  # m
ORIENTATION_NOISE_STD = 1e-3  # rad
LINVEL_NOISE_STD = 1e-2  # m/s
GYRO_NOISE_STD = 1e-2  # rad/s

# ENU → world frame rotation: 90° around z-axis
# ENU: x=East, y=North, z=Up → World: x=North(forward), y=West(left), z=Up
# R rotates ENU coords into world coords
_ENU_TO_WORLD = np.array(
    [
        [0.0, 1.0, 0.0],
        [-1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
)

_WORLD_TO_ENU = _ENU_TO_WORLD.T


class DroneAdapter:
    """Adapter for a 6-DOF quadrotor drone: ENU native ↔ IR.

    Args:
        entity_id: Unique identifier for this drone instance.
    """

    def __init__(self, entity_id: str = "drone_0") -> None:
        self._entity_id = entity_id

    @property
    def entity_id(self) -> str:
        return self._entity_id

    @property
    def fidelity_contract(self) -> FidelityContract:
        """Contract for native ↔ IR translation."""
        return FidelityContract(
            adapter_id=f"drone_adapter:{self._entity_id}",
            preserved_fields=["value", "unit", "frame", "timestamp", "clock_domain"],
            lost_fields=["rotor_speeds", "battery_level"],
            uncertainty_delta=POSITION_NOISE_STD,
            information_loss_estimate=0.05,
            notes="6-DOF pose and velocity preserved. Rotor speeds and battery not in IR. "
            "ENU↔world frame rotation applied. Body-frame gyro converted to world frame.",
        )

    def to_ir(self, native_state: dict[str, object]) -> IRState:
        """Convert native ENU state to canonical IR (world frame, SI).

        Args:
            native_state: Dict with keys:
                'position_enu': (3,) array in m (ENU frame)
                'orientation_quat_hamilton': (4,) array wxyz
                'linear_velocity_enu': (3,) array in m/s (ENU frame)
                'angular_velocity_body': (3,) array in rad/s (body frame)
                'time': float (sim seconds)

        Returns:
            IRState with Phytes in world frame, SI units.
        """
        pos_enu = np.asarray(native_state["position_enu"], dtype=np.float64)
        quat = np.asarray(native_state["orientation_quat_hamilton"], dtype=np.float64)
        linvel_enu = np.asarray(native_state["linear_velocity_enu"], dtype=np.float64)
        angvel_body = np.asarray(native_state["angular_velocity_body"], dtype=np.float64)
        sim_time = float(native_state["time"])  # type: ignore[arg-type]

        # Convert position: ENU → world
        pos_world = _ENU_TO_WORLD @ pos_enu

        # Quaternion: the orientation itself doesn't change representation,
        # but we compose with the ENU→world rotation
        R_body_enu = pr.matrix_from_quaternion(quat)
        R_body_world = _ENU_TO_WORLD @ R_body_enu
        quat_world = pr.quaternion_from_matrix(R_body_world)

        # Convert linear velocity: ENU → world
        linvel_world = _ENU_TO_WORLD @ linvel_enu

        # Convert angular velocity: body → world frame
        angvel_world = R_body_world @ angvel_body

        # Build SE(3) pose
        pose_se3 = make_se3(R_body_world, pos_world)

        base_prov = Provenance(
            chain=[
                ProvenanceEntry(
                    source=f"drone_sensor:{self._entity_id}",
                    operation="read_sensor",
                    timestamp=sim_time,
                )
            ],
            confidence=0.95,
        )

        phytes: dict[str, Phyte] = {}

        # Base pose (6-DOF: position + quaternion)
        phytes["base_pose"] = Phyte(
            semantic_id="base_pose",
            frame="world",
            pose=pose_se3,
            timestamp=sim_time,
            clock_domain="sim",
            unit="m",
            value=np.concatenate([pos_world, quat_world]),
            covariance=np.diag(
                [
                    POSITION_NOISE_STD**2,
                    POSITION_NOISE_STD**2,
                    POSITION_NOISE_STD**2,
                    ORIENTATION_NOISE_STD**2,
                    ORIENTATION_NOISE_STD**2,
                    ORIENTATION_NOISE_STD**2,
                    ORIENTATION_NOISE_STD**2,
                ]
            ),
            provenance=base_prov,
        )

        # Linear velocity (world frame)
        phytes["linear_velocity"] = Phyte(
            semantic_id="linear_velocity",
            frame="world",
            pose=identity_se3(),
            timestamp=sim_time,
            clock_domain="sim",
            unit="m/s",
            value=linvel_world,
            covariance=np.eye(3) * LINVEL_NOISE_STD**2,
            provenance=base_prov,
        )

        # Angular velocity (world frame)
        phytes["angular_velocity"] = Phyte(
            semantic_id="angular_velocity",
            frame="world",
            pose=identity_se3(),
            timestamp=sim_time,
            clock_domain="sim",
            unit="rad/s",
            value=angvel_world,
            covariance=np.eye(3) * GYRO_NOISE_STD**2,
            provenance=base_prov,
        )

        return IRState(
            entity_id=self._entity_id,
            phytes=phytes,
            timestamp=sim_time,
            clock_domain="sim",
        )

    def from_ir(self, ir_state: IRState) -> dict[str, object]:
        """Convert canonical IR back to native ENU state dict.

        Args:
            ir_state: IRState with Phytes in world frame.

        Returns:
            Dict with 'position_enu', 'orientation_quat_hamilton',
            'linear_velocity_enu', 'angular_velocity_body', 'time'.
        """
        # Extract pose
        pos_world = np.zeros(3)
        quat_world = np.array([1.0, 0.0, 0.0, 0.0])
        if "base_pose" in ir_state.phytes:
            val = ir_state.phytes["base_pose"].value
            pos_world = val[:3].copy()
            quat_world = val[3:7].copy()

        # Convert back: world → ENU
        pos_enu = _WORLD_TO_ENU @ pos_world

        # Reconstruct body rotation in ENU frame
        R_body_world = pr.matrix_from_quaternion(quat_world)
        R_body_enu = _WORLD_TO_ENU @ R_body_world
        quat_enu = pr.quaternion_from_matrix(R_body_enu)

        # Linear velocity: world → ENU
        linvel_world = np.zeros(3)
        if "linear_velocity" in ir_state.phytes:
            linvel_world = ir_state.phytes["linear_velocity"].value.copy()
        linvel_enu = _WORLD_TO_ENU @ linvel_world

        # Angular velocity: world → body frame
        angvel_world = np.zeros(3)
        if "angular_velocity" in ir_state.phytes:
            angvel_world = ir_state.phytes["angular_velocity"].value.copy()
        angvel_body = R_body_world.T @ angvel_world

        return {
            "position_enu": pos_enu,
            "orientation_quat_hamilton": quat_enu,
            "linear_velocity_enu": linvel_enu,
            "angular_velocity_body": angvel_body,
            "time": ir_state.timestamp,
        }
