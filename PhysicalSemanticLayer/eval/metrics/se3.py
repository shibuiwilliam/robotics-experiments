"""SE(3) distance metrics for evaluation.

Computes geodesic distance on the SE(3) Lie group between poses.
Used for round-trip fidelity measurement.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def se3_geodesic_distance(
    T1: NDArray[np.float64],
    T2: NDArray[np.float64],
    rot_weight: float = 1.0,
) -> float:
    """Geodesic distance on SE(3) between two 4×4 poses.

    Decomposes into translation error (m) and rotation error (rad),
    combined as ‖[trans_err, rot_weight * rot_err]‖₂.

    Args:
        T1: First 4×4 SE(3) matrix.
        T2: Second 4×4 SE(3) matrix.
        rot_weight: Weight for rotation component (default 1.0 = 1 rad ≈ 1 m).

    Returns:
        Non-negative scalar distance.
    """
    # Relative transform
    R1, t1 = T1[:3, :3], T1[:3, 3]
    R2, t2 = T2[:3, :3], T2[:3, 3]
    dR = R1.T @ R2
    dt = t2 - t1

    # Rotation angle from trace of relative rotation
    cos_angle = np.clip((np.trace(dR) - 1.0) / 2.0, -1.0, 1.0)
    angle = float(np.arccos(cos_angle))

    # Translation magnitude
    trans_err = float(np.linalg.norm(dt))

    return float(np.sqrt(trans_err**2 + (rot_weight * angle) ** 2))


def joint_rmse(
    predicted: NDArray[np.float64],
    ground_truth: NDArray[np.float64],
) -> float:
    """Root mean squared error between joint vectors.

    Args:
        predicted: Predicted joint values.
        ground_truth: Ground truth joint values.

    Returns:
        RMSE (same units as input, typically rad).
    """
    return float(np.sqrt(np.mean((predicted - ground_truth) ** 2)))


def joint_max_error(
    predicted: NDArray[np.float64],
    ground_truth: NDArray[np.float64],
) -> float:
    """Maximum absolute error across joints.

    Args:
        predicted: Predicted joint values.
        ground_truth: Ground truth joint values.

    Returns:
        Max absolute error.
    """
    return float(np.max(np.abs(predicted - ground_truth)))
