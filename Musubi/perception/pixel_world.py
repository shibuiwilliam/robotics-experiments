"""pixel→world: lift a camera pixel (+ metric depth) to a world coordinate.

Gemini Robotics ER returns normalized ``[y, x]`` points in 0..1000 (PROJECT.md §7.2). We map those
to image pixels, read the sim depth at that pixel, and back-project through the camera to world —
the calibrated bridge that lets ER's 2D points become Claims with world Poses (E0.5 rig).
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from sim.world import World


def normalized_yx_to_pixel(y: float, x: float, height: int, width: int) -> tuple[int, int]:
    """ER normalized [y, x] in 0..1000 → (row, col) pixel indices."""
    row = int(round(y / 1000.0 * (height - 1)))
    col = int(round(x / 1000.0 * (width - 1)))
    return max(0, min(height - 1, row)), max(0, min(width - 1, col))


def _camera_params(world: World, camera: str) -> tuple[np.ndarray, np.ndarray, float]:
    """(cam_xpos, cam_xmat 3x3, fovy_deg) for a named camera after mj_forward."""
    cam_id = mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_CAMERA, camera)
    if cam_id < 0:
        raise KeyError(f"unknown camera {camera!r}")
    pos = np.array(world.data.cam_xpos[cam_id], dtype=float)
    mat = np.array(world.data.cam_xmat[cam_id], dtype=float).reshape(3, 3)
    fovy = float(world.model.cam_fovy[cam_id])
    return pos, mat, fovy


def unproject(
    world: World,
    camera: str,
    row: int,
    col: int,
    depth: float,
    height: int,
    width: int,
) -> tuple[float, float, float]:
    """Back-project pixel (row, col) at metric ``depth`` to a world (x, y, z).

    MuJoCo cameras look down -z with +x right, +y up; renderer depth is distance along -z.
    """
    pos, mat, fovy = _camera_params(world, camera)
    f = 0.5 * height / math.tan(0.5 * math.radians(fovy))
    x_cam = (col - (width - 1) / 2.0) / f
    y_cam = -(row - (height - 1) / 2.0) / f
    point_cam = np.array([x_cam * depth, y_cam * depth, -depth], dtype=float)
    world_point = pos + mat @ point_cam
    return float(world_point[0]), float(world_point[1]), float(world_point[2])
