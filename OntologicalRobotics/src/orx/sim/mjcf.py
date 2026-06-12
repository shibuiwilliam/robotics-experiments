"""WorldConfig → MJCF XML ビルダ。

配置はシード派生RNGで決定的にジッタする。ゾーンが床より高い場合は静的な
プラットフォーム（棚板）を生成し、箱はその上に静定する。
"""

from __future__ import annotations

import math
from xml.etree import ElementTree as ET

import numpy as np

from orx.common.config import BoxConfig, WorldConfig, ZoneConfig

PLATFORM_THICKNESS = 0.04
_JITTER_FRAC = 0.15  # グリッドセル内ジッタ率


def body_name(box: str) -> str:
    return f"box_{box}"


def joint_name(box: str) -> str:
    return f"fj_{box}"


def camera_name(robot: str) -> str:
    return f"cam_{robot}"


def _zone_bottom(zone: ZoneConfig) -> float:
    return zone.center[2] - zone.size[2] / 2


def _camera_xyaxes(pos: tuple[float, ...], lookat: tuple[float, ...]) -> str:
    """MuJoCo カメラは自フレーム -Z を向く。lookat から xyaxes を計算する。"""
    p = np.asarray(pos, dtype=float)
    target = np.asarray(lookat, dtype=float)
    forward = target - p
    norm = np.linalg.norm(forward)
    if norm < 1e-9:
        raise ValueError("camera pos and lookat coincide")
    forward /= norm
    up = np.array([0.0, 0.0, 1.0])
    if abs(float(forward @ up)) > 0.999:
        up = np.array([0.0, 1.0, 0.0])
    x_cam = np.cross(forward, up)
    x_cam /= np.linalg.norm(x_cam)
    y_cam = np.cross(-forward, x_cam)
    return " ".join(f"{v:.6f}" for v in (*x_cam, *y_cam))


def box_placements(
    config: WorldConfig, rng: np.random.Generator
) -> dict[str, tuple[float, float, float]]:
    """ゾーン毎にグリッド＋ジッタで箱の初期位置を決める（決定的）。"""
    zones = {z.name: z for z in config.zones}
    by_zone: dict[str, list[BoxConfig]] = {}
    for box in config.boxes:
        if box.zone not in zones:
            raise ValueError(f"box {box.name!r} の初期ゾーン {box.zone!r} が未定義")
        by_zone.setdefault(box.zone, []).append(box)

    placements: dict[str, tuple[float, float, float]] = {}
    for zone_name, boxes in by_zone.items():
        zone = zones[zone_name]
        n = len(boxes)
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)
        usable_x = zone.size[0] * 0.7
        usable_y = zone.size[1] * 0.7
        for i, box in enumerate(boxes):
            r, c = divmod(i, cols)
            gx = (c + 0.5) / cols - 0.5
            gy = (r + 0.5) / rows - 0.5
            cell_w = usable_x / cols
            cell_h = usable_y / rows
            jx = float(rng.uniform(-_JITTER_FRAC, _JITTER_FRAC)) * cell_w
            jy = float(rng.uniform(-_JITTER_FRAC, _JITTER_FRAC)) * cell_h
            x = zone.center[0] + gx * usable_x + jx
            y = zone.center[1] + gy * usable_y + jy
            z = _zone_bottom(zone) + box.size + 0.002
            placements[box.name] = (x, y, z)
    return placements


def build_mjcf(config: WorldConfig, rng: np.random.Generator) -> str:
    placements = box_placements(config, rng)

    root = ET.Element("mujoco", model=config.name)
    ET.SubElement(root, "option", timestep=str(config.physics_dt))
    visual = ET.SubElement(root, "visual")
    ET.SubElement(visual, "global", offwidth="224", offheight="224")
    world = ET.SubElement(root, "worldbody")
    ET.SubElement(world, "light", pos="0 0 3", dir="0 0 -1", diffuse="0.9 0.9 0.9")
    ET.SubElement(
        world, "geom", name="floor", type="plane", size="10 10 0.1", rgba="0.35 0.35 0.38 1"
    )

    # ゾーンのプラットフォーム（床上ゾーンはスキップ）
    for zone in config.zones:
        bottom = _zone_bottom(zone)
        if bottom <= 0.01:
            continue
        ET.SubElement(
            world,
            "geom",
            name=f"platform_{zone.name}",
            type="box",
            pos=f"{zone.center[0]} {zone.center[1]} {bottom - PLATFORM_THICKNESS / 2}",
            size=f"{zone.size[0] / 2} {zone.size[1] / 2} {PLATFORM_THICKNESS / 2}",
            rgba="0.55 0.45 0.35 1",
        )

    for box in config.boxes:
        x, y, z = placements[box.name]
        body = ET.SubElement(world, "body", name=body_name(box.name), pos=f"{x} {y} {z}")
        ET.SubElement(body, "freejoint", name=joint_name(box.name))
        ET.SubElement(
            body,
            "geom",
            name=f"geom_{box.name}",
            type="box",
            size=f"{box.size} {box.size} {box.size}",
            rgba=" ".join(str(v) for v in box.rgba),
            mass="0.5",
        )

    for robot in config.robots:
        cam = robot.camera
        body = ET.SubElement(
            world, "body", name=f"robot_{robot.name}", pos=" ".join(str(v) for v in cam.pos)
        )
        # カメラ基部（衝突なし・描画のみ）
        ET.SubElement(
            body,
            "geom",
            name=f"robotbase_{robot.name}",
            type="sphere",
            size="0.05",
            rgba="0.2 0.2 0.7 1",
            contype="0",
            conaffinity="0",
        )
        ET.SubElement(
            body,
            "camera",
            name=camera_name(robot.name),
            pos="0 0 0",
            xyaxes=_camera_xyaxes(cam.pos, cam.lookat),
            fovy=str(cam.fovy),
        )

    return ET.tostring(root, encoding="unicode")
