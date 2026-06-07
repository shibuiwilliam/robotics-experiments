"""SE(3) geometry primitives — thin wrapper around pytransform3d.

All poses are stored as 4×4 homogeneous transformation matrices (SE(3)).
Rotations are stored as 3×3 matrices (SO(3)).
Frame names are always explicit — no implicit "world" frame.

This module is the **only** place where pytransform3d is called directly,
keeping the rest of the codebase decoupled from the specific geometry library.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from pytransform3d import rotations as pr
from pytransform3d import transformations as pt

# ──────────────────────────────────────────────
# Type aliases
# ──────────────────────────────────────────────

Mat4 = NDArray[np.float64]  # 4×4 homogeneous
Mat3 = NDArray[np.float64]  # 3×3 rotation
Vec3 = NDArray[np.float64]  # (3,) translation


def identity_se3() -> Mat4:
    """Return the 4×4 identity (no translation, no rotation)."""
    return np.eye(4)


def make_se3(rotation: Mat3, translation: Vec3) -> Mat4:
    """Build an SE(3) matrix from rotation (3×3) and translation (3,).

    Args:
        rotation: SO(3) rotation matrix.
        translation: (3,) translation vector in the *parent* frame.

    Returns:
        4×4 homogeneous transformation matrix.
    """
    T = np.eye(4)
    T[:3, :3] = rotation
    T[:3, 3] = translation
    return T


def rotation_of(T: Mat4) -> Mat3:
    """Extract the 3×3 rotation from a 4×4 SE(3) matrix."""
    return T[:3, :3].copy()


def translation_of(T: Mat4) -> Vec3:
    """Extract the (3,) translation from a 4×4 SE(3) matrix."""
    return T[:3, 3].copy()


def inverse_se3(T: Mat4) -> Mat4:
    """Invert an SE(3) transformation matrix."""
    return pt.invert_transform(T)


def compose_se3(T1: Mat4, T2: Mat4) -> Mat4:
    """Compose two SE(3) transformations: T1 ∘ T2."""
    return pt.concat(T2, T1)


def se3_distance(T1: Mat4, T2: Mat4, *, rot_weight: float = 1.0) -> float:
    """Geodesic distance on SE(3) between two poses.

    Uses the Lie-group logarithm: ‖log(T1⁻¹ T2)‖.
    The rotation component (radians) is weighted by *rot_weight*
    relative to translation (metres).

    Args:
        T1: First SE(3) pose.
        T2: Second SE(3) pose.
        rot_weight: Scalar weight for the rotation component (default 1.0).

    Returns:
        Non-negative scalar distance.
    """
    delta = compose_se3(inverse_se3(T1), T2)
    # Decompose into rotation angle and translation magnitude
    angle = np.arccos(np.clip((np.trace(rotation_of(delta)) - 1.0) / 2.0, -1.0, 1.0))
    trans = np.linalg.norm(translation_of(delta))
    return float(np.sqrt(trans**2 + (rot_weight * angle) ** 2))


def random_se3(rng: np.random.Generator) -> Mat4:
    """Sample a uniformly random SE(3) transformation.

    Translation sampled from N(0, 1)³; rotation uniform on SO(3).

    Args:
        rng: numpy random generator (seeded for reproducibility).

    Returns:
        4×4 homogeneous transformation matrix.
    """
    q = pr.random_quaternion(rng)
    R = pr.matrix_from_quaternion(q)
    t = rng.standard_normal(3)
    return make_se3(R, t)


def rotation_x(angle_rad: float) -> Mat3:
    """Rotation matrix about x-axis by *angle_rad*."""
    return pr.active_matrix_from_angle(0, angle_rad)


def rotation_y(angle_rad: float) -> Mat3:
    """Rotation matrix about y-axis by *angle_rad*."""
    return pr.active_matrix_from_angle(1, angle_rad)


def rotation_z(angle_rad: float) -> Mat3:
    """Rotation matrix about z-axis by *angle_rad*."""
    return pr.active_matrix_from_angle(2, angle_rad)
