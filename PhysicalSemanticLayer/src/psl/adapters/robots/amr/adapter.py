"""AMR (Autonomous Mobile Robot) adapter — velocity-controlled mobile base.

Intentionally heterogeneous from the Panda adapter:
  - Velocity control (not position)
  - mm units (not m)
  - y-up frame convention (not z-up)
  - Different joint naming (base_x, base_y, base_yaw)

This adapter converts between the AMR's native schema and the
Canonical IR (SI, world frame, Phytes).
"""

from __future__ import annotations

import numpy as np
from pytransform3d import rotations as pr

from psl.contracts.core import FidelityContract
from psl.ir.core import IRState
from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3, make_se3, rotation_x
from psl.phyte.provenance import Provenance, ProvenanceEntry

# AMR native schema constants
AMR_UNIT_SCALE = 1000.0  # mm per m
AMR_NOISE_STD_POS = 0.005  # 5mm position noise in m
AMR_NOISE_STD_VEL = 0.01  # velocity noise

# Frame transform: y-up to z-up is a -90deg rotation around x
_Y_UP_TO_Z_UP = rotation_x(-np.pi / 2)
_Z_UP_TO_Y_UP = rotation_x(np.pi / 2)


class AMRAdapter:
    """Adapter for velocity-controlled mobile base.

    Native schema:
      - base_pos_mm: [x, y] in mm, y-up frame
      - base_yaw_deg: heading in degrees
      - base_vel_mm_s: [vx, vy] in mm/s
      - base_yaw_rate_deg_s: yaw rate in deg/s
      - time: sim time

    Canonical IR:
      - Phytes in m, z-up, rad, world frame

    Args:
        entity_id: Unique identifier for this AMR.
    """

    def __init__(self, entity_id: str = "amr_0") -> None:
        self._entity_id = entity_id

    @property
    def entity_id(self) -> str:
        return self._entity_id

    @property
    def fidelity_contract(self) -> FidelityContract:
        return FidelityContract(
            adapter_id=f"amr_adapter:{self._entity_id}",
            preserved_fields=["value", "unit", "frame", "timestamp", "clock_domain"],
            lost_fields=[],
            uncertainty_delta=AMR_NOISE_STD_POS,
            information_loss_estimate=0.02,
            notes="AMR adapter: mm->m, y-up->z-up, deg->rad conversions. "
            "Small information loss from frame rotation numerics.",
        )

    def to_ir(self, native_state: dict[str, object]) -> IRState:
        """Convert AMR native (mm, y-up, deg) → canonical IR (m, z-up, rad).

        Args:
            native_state: AMR native state dict.

        Returns:
            IRState in canonical form.
        """
        pos_mm = np.asarray(native_state["base_pos_mm"], dtype=np.float64)
        yaw_deg = float(native_state["base_yaw_deg"])  # type: ignore[arg-type]
        vel_mm = np.asarray(native_state["base_vel_mm_s"], dtype=np.float64)
        yaw_rate_deg = float(native_state["base_yaw_rate_deg_s"])  # type: ignore[arg-type]
        sim_time = float(native_state["time"])  # type: ignore[arg-type]

        # Convert units: mm → m, deg → rad
        pos_m = pos_mm / AMR_UNIT_SCALE
        yaw_rad = np.deg2rad(yaw_deg)
        vel_m = vel_mm / AMR_UNIT_SCALE
        yaw_rate_rad = np.deg2rad(yaw_rate_deg)

        # Build pose in z-up world frame
        # AMR native is y-up, so pos_mm[0] = x_yup, pos_mm[1] = y_yup
        # In z-up: x_zup = x_yup, y_zup = 0 (ground), z_zup = y_yup (height? no, AMR is 2D)
        # For a 2D mobile base on ground plane: x=x, y=y, z=0 in z-up
        pos_world = np.array([pos_m[0], pos_m[1], 0.0])

        R_yaw = pr.active_matrix_from_angle(2, yaw_rad)  # rotation about z
        pose = make_se3(R_yaw, pos_world)

        prov = Provenance(
            chain=[
                ProvenanceEntry(
                    source=f"amr_sensor:{self._entity_id}",
                    operation="read_sensor_mm_yup",
                    timestamp=sim_time,
                )
            ],
            confidence=0.95,
        )

        pos_cov = np.eye(3) * AMR_NOISE_STD_POS**2
        vel_cov = np.eye(3) * AMR_NOISE_STD_VEL**2

        phytes: dict[str, Phyte] = {
            "base_pose": Phyte(
                semantic_id="base_pose",
                frame="world",
                pose=pose,
                timestamp=sim_time,
                clock_domain="sim",
                unit="m",
                value=pos_world,
                covariance=pos_cov,
                provenance=prov,
            ),
            "base_velocity": Phyte(
                semantic_id="base_velocity",
                frame="world",
                pose=identity_se3(),
                timestamp=sim_time,
                clock_domain="sim",
                unit="m/s",
                value=np.array([vel_m[0], vel_m[1], yaw_rate_rad]),
                covariance=vel_cov,
                provenance=prov,
            ),
        }

        return IRState(
            entity_id=self._entity_id,
            phytes=phytes,
            timestamp=sim_time,
            clock_domain="sim",
        )

    def from_ir(self, ir_state: IRState) -> dict[str, object]:
        """Convert canonical IR → AMR native (mm, y-up, deg).

        Args:
            ir_state: Canonical IRState.

        Returns:
            AMR native state dict.
        """
        pos_world = np.zeros(2)
        yaw_rad = 0.0
        vel_m = np.zeros(2)
        yaw_rate_rad = 0.0

        if "base_pose" in ir_state.phytes:
            val = ir_state.phytes["base_pose"].value
            pos_world = val[:2].copy()
            # Extract yaw from pose matrix
            pose = ir_state.phytes["base_pose"].pose
            yaw_rad = float(np.arctan2(pose[1, 0], pose[0, 0]))

        if "base_velocity" in ir_state.phytes:
            val = ir_state.phytes["base_velocity"].value
            vel_m = val[:2].copy()
            yaw_rate_rad = float(val[2]) if val.size > 2 else 0.0

        return {
            "base_pos_mm": pos_world * AMR_UNIT_SCALE,
            "base_yaw_deg": float(np.rad2deg(yaw_rad)),
            "base_vel_mm_s": vel_m * AMR_UNIT_SCALE,
            "base_yaw_rate_deg_s": float(np.rad2deg(yaw_rate_rad)),
            "time": ir_state.timestamp,
        }
