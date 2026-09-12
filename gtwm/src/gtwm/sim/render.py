"""オフスクリーンレンダリングと遮蔽率の計算。

`mujoco.Renderer` はメインスレッドでのみ生成・使用する（macOS では EGL/OSMesa が
使えないため）。1つのレンダラーを使い回し、RGB / セグメンテーション / 深度を
モード切替で取得する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np

GEOM_OBJ_TYPE = int(mujoco.mjtObj.mjOBJ_GEOM)


@dataclass
class CameraFrame:
    """1台のカメラ・1時刻ぶんの観測。"""

    rgb: np.ndarray  # uint8 [H,W,3]
    seg_id: np.ndarray  # int32 [H,W] geom id（背景は -1）
    seg_type: np.ndarray  # int32 [H,W] mjtObj 種別（背景は -1）
    depth: np.ndarray  # float32 [H,W]


def render_camera(renderer: mujoco.Renderer, data: mujoco.MjData, cam_name: str) -> CameraFrame:
    """指定カメラの RGB・セグメンテーション・深度を1回ずつ取得する。"""
    renderer.update_scene(data, camera=cam_name)
    rgb = renderer.render().copy()

    renderer.enable_segmentation_rendering()
    renderer.update_scene(data, camera=cam_name)
    seg = renderer.render().copy()
    renderer.disable_segmentation_rendering()

    renderer.enable_depth_rendering()
    renderer.update_scene(data, camera=cam_name)
    depth = renderer.render().copy()
    renderer.disable_depth_rendering()

    return CameraFrame(
        rgb=rgb, seg_id=seg[..., 0], seg_type=seg[..., 1], depth=depth.astype(np.float32)
    )


def camera_rotation_matrix(data: mujoco.MjData, cam_id: int) -> np.ndarray:
    """カメラのワールド回転行列（列がカメラのローカル軸）を返す。"""
    return np.asarray(data.cam_xmat[cam_id]).reshape(3, 3)


def project_point(
    cam_pos: np.ndarray,
    cam_rot: np.ndarray,
    fovy_deg: float,
    width: int,
    height: int,
    point_world: np.ndarray,
) -> tuple[float, float, float]:
    """世界座標点をピクセル座標に投影する。

    MuJoCo のカメラ規約：ローカル -Z が視線方向、+X が右、+Y が上。
    戻り値は (px, py, depth)。depth<=0 はカメラの後ろ（画角外）。
    """
    rel_world = point_world - cam_pos
    rel_local = cam_rot.T @ rel_world
    depth = -rel_local[2]
    if depth <= 1e-6:
        return (math.nan, math.nan, depth)
    f = height / (2.0 * math.tan(math.radians(fovy_deg) / 2.0))
    px = width / 2.0 + f * (rel_local[0] / depth)
    py = height / 2.0 - f * (rel_local[1] / depth)
    return (px, py, depth)


def projected_bbox_area_px(
    cam_pos: np.ndarray,
    cam_rot: np.ndarray,
    fovy_deg: float,
    width: int,
    height: int,
    center: np.ndarray,
    half_extents: np.ndarray,
) -> float:
    """物体の軸並行境界ボックスをカメラに投影し、画面内の外接矩形面積（px^2）を返す。

    「深度無視の投影面積」＝シルエットを解析的に投影した面積であり、遮蔽の有無を
    問わない幾何学的な射影面積。真のシルエットではなく外接矩形での近似。
    """
    signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
    corners = center[None, :] + signs * half_extents[None, :]
    pxs = []
    pys = []
    any_in_front = False
    for corner in corners:
        px, py, depth = project_point(cam_pos, cam_rot, fovy_deg, width, height, corner)
        if depth > 1e-6:
            any_in_front = True
            pxs.append(px)
            pys.append(py)
    if not any_in_front or not pxs:
        return 0.0
    x0, x1 = max(min(pxs), 0.0), min(max(pxs), float(width))
    y0, y1 = max(min(pys), 0.0), min(max(pys), float(height))
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return float(area)


def occlusion_rate(
    seg_id: np.ndarray, seg_type: np.ndarray, geom_id: int, projected_area_px: float
) -> float:
    """遮蔽率 = 1 - (可視画素数 / 投影面積)。範囲外・投影面積0は全遮蔽(1.0)扱い。"""
    visible = int(np.count_nonzero((seg_id == geom_id) & (seg_type == GEOM_OBJ_TYPE)))
    if projected_area_px <= 1e-6:
        return 1.0
    rate = 1.0 - min(visible / projected_area_px, 1.0)
    return float(max(0.0, min(1.0, rate)))
