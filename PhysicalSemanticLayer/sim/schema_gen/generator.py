"""Schema Generator — controlled heterogeneity dial for dose-response testing.

Applies composable transformations to a base robot state to create
artificial schema mismatches: unit rescaling, frame rotation, name
remapping, noise injection, control mode changes.

This is the independent variable in our experiments — turning the
heterogeneity dial up produces the dose-response curve (PROJECT.md §6.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from psl.phyte.geometry import rotation_z


@dataclass(frozen=True)
class SchemaTransform:
    """A single schema transformation — one axis of heterogeneity.

    These compose: applying multiple transforms increases the heterogeneity dose.

    Fields:
        unit_scale: Multiply joint values by this factor (e.g., 1000 for rad→mrad).
        unit_name: New unit string after scaling (e.g., 'mrad').
        frame_rotation_z_rad: Rotate the robot's frame about z by this angle.
        name_remap: Dict mapping original joint names to new names.
        sensor_noise_std: Additional Gaussian noise std to inject.
        position_offset: Offset to add to EE position (simulates frame origin difference).
    """

    unit_scale: float = 1.0
    unit_name: str = "rad"
    frame_rotation_z_rad: float = 0.0
    name_remap: dict[str, str] = field(default_factory=dict)
    sensor_noise_std: float = 0.0
    position_offset: NDArray[np.float64] = field(default_factory=lambda: np.zeros(3))


def apply_schema_transform(
    native_state: dict[str, object],
    transform: SchemaTransform,
    rng: np.random.Generator | None = None,
) -> dict[str, object]:
    """Apply a schema transform to a native state dict.

    Simulates what a heterogeneous robot would report:
    different units, different frame, different naming, different noise.

    Args:
        native_state: Base (canonical) native state.
        transform: The schema transform to apply.
        rng: Random generator for noise injection.

    Returns:
        Transformed native state (as the heterogeneous robot would see it).
    """
    jpos = np.asarray(native_state["joint_positions"], dtype=np.float64).copy()
    jvel = np.asarray(native_state["joint_velocities"], dtype=np.float64).copy()
    ee_pos = np.asarray(native_state["ee_position"], dtype=np.float64).copy()
    ee_quat = np.asarray(native_state["ee_quaternion"], dtype=np.float64).copy()

    # Unit scaling
    jpos = jpos * transform.unit_scale
    jvel = jvel * transform.unit_scale

    # Frame rotation (rotate EE position around z)
    if abs(transform.frame_rotation_z_rad) > 1e-12:
        R = rotation_z(transform.frame_rotation_z_rad)
        ee_pos = R @ ee_pos

    # Position offset (different frame origin)
    ee_pos = ee_pos + transform.position_offset

    # Noise injection
    if transform.sensor_noise_std > 0 and rng is not None:
        jpos += rng.normal(0, transform.sensor_noise_std, size=jpos.shape)
        jvel += rng.normal(0, transform.sensor_noise_std, size=jvel.shape)
        ee_pos += rng.normal(0, transform.sensor_noise_std, size=ee_pos.shape)

    return {
        "joint_positions": jpos,
        "joint_velocities": jvel,
        "ee_position": ee_pos,
        "ee_quaternion": ee_quat,
        "time": native_state["time"],
    }


def invert_schema_transform(
    transformed_state: dict[str, object],
    transform: SchemaTransform,
) -> dict[str, object]:
    """Invert a schema transform (undo unit scaling, frame rotation, offset).

    Note: noise injection is NOT invertible — this is intentional,
    as it represents irreversible information loss.

    Args:
        transformed_state: State in the heterogeneous robot's schema.
        transform: The transform to invert.

    Returns:
        State back in the base (canonical) schema (modulo noise).
    """
    jpos = np.asarray(transformed_state["joint_positions"], dtype=np.float64).copy()
    jvel = np.asarray(transformed_state["joint_velocities"], dtype=np.float64).copy()
    ee_pos = np.asarray(transformed_state["ee_position"], dtype=np.float64).copy()
    ee_quat = np.asarray(transformed_state["ee_quaternion"], dtype=np.float64).copy()

    # Undo position offset
    ee_pos = ee_pos - transform.position_offset

    # Undo frame rotation
    if abs(transform.frame_rotation_z_rad) > 1e-12:
        R_inv = rotation_z(-transform.frame_rotation_z_rad)
        ee_pos = R_inv @ ee_pos

    # Undo unit scaling
    if abs(transform.unit_scale) > 1e-12:
        jpos = jpos / transform.unit_scale
        jvel = jvel / transform.unit_scale

    return {
        "joint_positions": jpos,
        "joint_velocities": jvel,
        "ee_position": ee_pos,
        "ee_quaternion": ee_quat,
        "time": transformed_state["time"],
    }


# Pre-defined heterogeneity doses for sweep experiments
HETEROGENEITY_DOSES: list[SchemaTransform] = [
    SchemaTransform(),  # Dose 0: no change (baseline)
    SchemaTransform(unit_scale=1000.0, unit_name="mrad"),  # Dose 1: unit mismatch
    SchemaTransform(frame_rotation_z_rad=np.pi / 4),  # Dose 2: frame rotation 45deg
    SchemaTransform(sensor_noise_std=0.01),  # Dose 3: moderate noise
    SchemaTransform(  # Dose 4: unit + frame + noise
        unit_scale=1000.0,
        unit_name="mrad",
        frame_rotation_z_rad=np.pi / 4,
        sensor_noise_std=0.01,
    ),
    SchemaTransform(  # Dose 5: everything + offset
        unit_scale=1000.0,
        unit_name="mrad",
        frame_rotation_z_rad=np.pi / 2,
        sensor_noise_std=0.05,
        position_offset=np.array([0.5, 0.0, 0.0]),
    ),
]
