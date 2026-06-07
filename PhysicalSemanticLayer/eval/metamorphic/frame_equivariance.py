"""Metamorphic relation: Frame equivariance.

Property: T(g · x) == g · T(x) for any SE(3) transform g.

If we apply a rigid-body transform g to the native state before translation,
the IR result should equal applying g to the IR result of the untransformed state.

This is a label-free test — no ground truth needed.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from psl.ir.core import Adapter
from psl.phyte.geometry import compose_se3, make_se3, se3_distance


def apply_frame_transform_to_native(
    native_state: dict[str, object],
    g: NDArray[np.float64],
) -> dict[str, object]:
    """Apply an SE(3) transform g to a native state dict.

    Transforms joint positions are rotation-invariant (they're angles),
    but EE position/orientation must be transformed.

    Args:
        native_state: Native state dict.
        g: 4x4 SE(3) transform.

    Returns:
        Transformed native state dict.
    """
    ee_pos = np.asarray(native_state["ee_position"], dtype=np.float64)
    ee_quat = np.asarray(native_state["ee_quaternion"], dtype=np.float64)

    from pytransform3d import rotations as pr

    # Build SE(3) for EE
    R_ee = pr.matrix_from_quaternion(ee_quat)
    T_ee = make_se3(R_ee, ee_pos)

    # Apply transform
    T_new = compose_se3(g, T_ee)
    new_pos = T_new[:3, 3]
    new_R = T_new[:3, :3]
    new_quat = pr.quaternion_from_matrix(new_R)

    return {
        **native_state,
        "ee_position": new_pos.copy(),
        "ee_quaternion": new_quat.copy(),
    }


def check_frame_equivariance(
    native_state: dict[str, object],
    adapter: Adapter,
    g: NDArray[np.float64],
    tol: float = 1e-6,
) -> tuple[bool, float]:
    """Check T(g . x) ~ g . T(x) for the EE pose Phyte.

    Args:
        native_state: Original native state.
        adapter: Adapter with to_ir() method.
        g: SE(3) transform to test.
        tol: Tolerance for the distance check.

    Returns:
        (passed, divergence) -- passed is True if divergence < tol.
    """
    # Path 1: transform then translate: T(g . x)
    transformed_native = apply_frame_transform_to_native(native_state, g)
    ir_transformed = adapter.to_ir(transformed_native)

    # Path 2: translate then transform: g . T(x)
    ir_original = adapter.to_ir(native_state)

    # Compare EE poses
    if "ee_pose" not in ir_transformed.phytes or "ee_pose" not in ir_original.phytes:
        return True, 0.0  # No EE pose to compare

    pose_path1 = ir_transformed.phytes["ee_pose"].pose
    pose_path2_raw = ir_original.phytes["ee_pose"].pose
    pose_path2 = compose_se3(g, pose_path2_raw)

    divergence = se3_distance(pose_path1, pose_path2)
    return divergence < tol, divergence
