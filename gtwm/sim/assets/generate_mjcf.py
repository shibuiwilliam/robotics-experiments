"""倉庫 MJCF アセットと registry.yaml を生成する一回限りのスクリプト。

sim/assets/warehouse.xml と include/*.xml は本スクリプトの出力を静的ファイルとして
コミットしたものである。レイアウトを変える場合はこのスクリプトを編集して再実行する。
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import yaml

ASSETS_DIR = Path(__file__).parent
INCLUDE_DIR = ASSETS_DIR / "include"

FLOOR_HALF_X = 6.0
FLOOR_HALF_Y = 4.0

ZONES = [
    ("Dock_In", -5.0),
    ("Inspect", -3.0),
    ("Storage_A", -1.0),
    ("Storage_B", 1.0),
    ("Pick", 3.0),
    ("Dock_Out", 5.0),
]
ZONE_HALF_X = 1.0
ZONE_COLORS = [
    "0.85 0.55 0.55 0.25",
    "0.85 0.75 0.45 0.25",
    "0.55 0.75 0.85 0.25",
    "0.55 0.85 0.65 0.25",
    "0.75 0.55 0.85 0.25",
    "0.85 0.55 0.75 0.25",
]

RACKS = [
    ("1", -1.5, -2.0),
    ("2", -0.5, 2.0),
    ("3", 0.5, -2.0),
    ("4", 1.5, 2.0),
]
RACK_LEVELS = [0.3, 0.9, 1.5]
RACK_BAYS = [-0.6, 0.0, 0.6]

DOCKS = [("1", -FLOOR_HALF_X, "Dock_In"), ("2", FLOOR_HALF_X, "Dock_Out")]
GATES = [("1", -4.0), ("2", 4.0)]
CONVEYOR_X = 3.0
SCALE_SITE_POS = (-3.0, -3.0, 0.05)

AGV_START = [(-5.0, -3.0), (5.0, 3.0)]
WORKER_START = [(-3.0, 3.2), (1.0, 3.2), (3.0, -3.2)]

N_PALLETS = 20
N_CASES = 40
PALLET_HALF = (0.30, 0.20, 0.075)
CASE_HALF = (0.10, 0.10, 0.10)

CAMERAS = [
    ("1", (-FLOOR_HALF_X + 0.3, -FLOOR_HALF_Y + 0.3, 4.5)),
    ("2", (FLOOR_HALF_X - 0.3, -FLOOR_HALF_Y + 0.3, 4.5)),
    ("3", (-FLOOR_HALF_X + 0.3, FLOOR_HALF_Y - 0.3, 4.5)),
    ("4", (FLOOR_HALF_X - 0.3, FLOOR_HALF_Y - 0.3, 4.5)),
]
CAMERA_TARGET = np.array([0.0, 0.0, 0.3])
CAMERA_FOVY = 55


def look_at_quat(cam_pos: np.ndarray, target: np.ndarray) -> str:
    """MuJoCo カメラ規約（ローカル -Z が視線方向）に合う quaternion "w x y z" を返す。"""
    forward = target - cam_pos
    forward = forward / np.linalg.norm(forward)
    world_up = np.array([0.0, 0.0, 1.0])
    right = np.cross(forward, world_up)
    right = right / np.linalg.norm(right)
    up = np.cross(right, forward)
    # カメラのローカル軸：x=right, y=up, z=-forward
    rot = np.stack([right, up, -forward], axis=1)
    trace = np.trace(rot)
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        w = 0.25 * s
        x = (rot[2, 1] - rot[1, 2]) / s
        y = (rot[0, 2] - rot[2, 0]) / s
        z = (rot[1, 0] - rot[0, 1]) / s
    else:
        i = np.argmax([rot[0, 0], rot[1, 1], rot[2, 2]])
        if i == 0:
            s = math.sqrt(1.0 + rot[0, 0] - rot[1, 1] - rot[2, 2]) * 2
            w = (rot[2, 1] - rot[1, 2]) / s
            x = 0.25 * s
            y = (rot[0, 1] + rot[1, 0]) / s
            z = (rot[0, 2] + rot[2, 0]) / s
        elif i == 1:
            s = math.sqrt(1.0 + rot[1, 1] - rot[0, 0] - rot[2, 2]) * 2
            w = (rot[0, 2] - rot[2, 0]) / s
            x = (rot[0, 1] + rot[1, 0]) / s
            y = 0.25 * s
            z = (rot[1, 2] + rot[2, 1]) / s
        else:
            s = math.sqrt(1.0 + rot[2, 2] - rot[0, 0] - rot[1, 1]) * 2
            w = (rot[1, 0] - rot[0, 1]) / s
            x = (rot[0, 2] + rot[2, 0]) / s
            y = (rot[1, 2] + rot[2, 1]) / s
            z = 0.25 * s
    return f"{w:.6f} {x:.6f} {y:.6f} {z:.6f}"


def rack_footprint(rx: float, ry: float) -> tuple[float, float, float, float]:
    return (rx - 0.5, rx + 0.5, ry - 1.0, ry + 1.0)


def forbidden_regions() -> list[tuple[float, float, float, float]]:
    regions = [rack_footprint(rx, ry) for _, rx, ry in RACKS]
    regions.append((CONVEYOR_X - 0.5, CONVEYOR_X + 0.5, -3.2, 3.2))
    regions.append(
        (
            SCALE_SITE_POS[0] - 0.4,
            SCALE_SITE_POS[0] + 0.4,
            SCALE_SITE_POS[1] - 0.4,
            SCALE_SITE_POS[1] + 0.4,
        )
    )
    return regions


def is_forbidden(x: float, y: float, regions: list[tuple[float, float, float, float]]) -> bool:
    return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, x1, y0, y1 in regions)


def free_grid_cells(spacing: float, margin: float) -> list[tuple[float, float]]:
    regions = forbidden_regions()
    xs = np.arange(-FLOOR_HALF_X + margin, FLOOR_HALF_X - margin + 1e-9, spacing)
    ys = np.arange(-FLOOR_HALF_Y + margin, FLOOR_HALF_Y - margin + 1e-9, spacing)
    cells = []
    for x in xs:
        for y in ys:
            if not is_forbidden(x, y, regions):
                cells.append((round(float(x), 3), round(float(y), 3)))
    return cells


def gen_zones() -> str:
    lines = ["<mujocoinclude>"]
    for i, (name, xc) in enumerate(ZONES):
        lines.append(
            f'  <site name="zone:{name}" type="box" size="{ZONE_HALF_X} {FLOOR_HALF_Y} 0.001" '
            f'pos="{xc} 0 0.001" rgba="{ZONE_COLORS[i]}" group="1"/>'
        )
    lines.append(
        f'  <site name="gate:1" type="box" size="0.02 {FLOOR_HALF_Y} 0.3" '  # noqa: E501
        f'pos="{GATES[0][1]} 0 0.3" rgba="0.9 0.1 0.1 0.4" group="2"/>'
    )
    lines.append(
        f'  <site name="gate:2" type="box" size="0.02 {FLOOR_HALF_Y} 0.3" '  # noqa: E501
        f'pos="{GATES[1][1]} 0 0.3" rgba="0.9 0.1 0.1 0.4" group="2"/>'
    )
    lines.append(
        f'  <site name="scale:1" type="box" size="0.35 0.35 0.02" pos="{SCALE_SITE_POS[0]} {SCALE_SITE_POS[1]} 0.02" '  # noqa: E501
        f'rgba="0.2 0.2 0.9 0.4" group="2"/>'
    )
    lines.append("</mujocoinclude>")
    return "\n".join(lines)


def gen_racks() -> str:
    lines = ["<mujocoinclude>"]
    for rack_id, rx, ry in RACKS:
        lines.append(f'  <body name="rack:{rack_id}" pos="{rx} {ry} 0">')
        lines.append(
            '    <geom type="box" size="0.45 0.85 0.9" pos="0 0 0.9" rgba="0.55 0.4 0.3 0.35" '
            'contype="1" conaffinity="1"/>'
        )
        for level_i, lz in enumerate(RACK_LEVELS):
            for bay_i, by in enumerate(RACK_BAYS):
                lines.append(
                    f'    <site name="slot:{rack_id}-{level_i}-{bay_i}" type="sphere" size="0.03" '
                    f'pos="0 {by} {lz}" rgba="0.9 0.9 0.1 0.6" group="3"/>'
                )
        lines.append("  </body>")
    lines.append("</mujocoinclude>")
    return "\n".join(lines)


def gen_docks() -> str:
    lines = ["<mujocoinclude>"]
    for dock_id, x, _zone in DOCKS:
        lines.append(f'  <body name="dock:{dock_id}" pos="{x} 0 0.75">')
        lines.append('    <geom type="box" size="0.05 1.2 0.75" rgba="0.3 0.3 0.3 0.8"/>')
        lines.append(
            f'    <site name="dock:{dock_id}" type="box" size="0.4 1.2 0.75" pos="0 0 0" '
            'rgba="0.3 0.3 0.3 0.0" group="2"/>'
        )
        lines.append("  </body>")
    lines.append("</mujocoinclude>")
    return "\n".join(lines)


def gen_conveyor() -> str:
    lines = ["<mujocoinclude>"]
    lines.append(f'  <body name="equipment:conveyor_1" pos="{CONVEYOR_X} 0 0.4">')
    lines.append('    <geom type="box" size="0.4 3.0 0.05" rgba="0.2 0.2 0.2 1"/>')
    lines.append(
        '    <site name="equipment:conveyor_1" type="box" size="0.4 3.0 0.05" pos="0 0 0.06" '
        'rgba="0.2 0.2 0.2 0.0" group="2"/>'
    )
    lines.append("  </body>")
    lines.append("</mujocoinclude>")
    return "\n".join(lines)


def gen_agvs() -> str:
    lines = ["<mujocoinclude>"]
    for i, (x, y) in enumerate(AGV_START, start=1):
        lines.append(f'  <body name="agv:{i}" pos="{x} {y} 0.15">')
        lines.append(f'    <joint name="agv:{i}_x" type="slide" axis="1 0 0" damping="5"/>')
        lines.append(f'    <joint name="agv:{i}_y" type="slide" axis="0 1 0" damping="5"/>')
        lines.append(f'    <joint name="agv:{i}_yaw" type="hinge" axis="0 0 1" damping="2"/>')
        lines.append(
            f'    <geom name="agv:{i}" type="box" size="0.3 0.25 0.15" '
            'rgba="0.9 0.6 0.1 1" mass="60"/>'
        )
        lines.append("  </body>")
    lines.append("</mujocoinclude>")
    return "\n".join(lines)


def gen_agv_actuators() -> str:
    lines = ["<mujocoinclude>"]
    for i in range(1, len(AGV_START) + 1):
        lines.append(f'  <velocity name="agv:{i}_x_act" joint="agv:{i}_x" kv="200"/>')
        lines.append(f'  <velocity name="agv:{i}_y_act" joint="agv:{i}_y" kv="200"/>')
        lines.append(f'  <velocity name="agv:{i}_yaw_act" joint="agv:{i}_yaw" kv="50"/>')
    lines.append("</mujocoinclude>")
    return "\n".join(lines)


def gen_workers() -> str:
    lines = ["<mujocoinclude>"]
    for i, (x, y) in enumerate(WORKER_START, start=1):
        lines.append(f'  <body name="worker:{i}" mocap="true" pos="{x} {y} 0.5">')
        lines.append(
            '    <geom type="capsule" fromto="0 0 -0.4 0 0 0.4" size="0.18" rgba="0.2 0.6 0.9 1" '
            'contype="0" conaffinity="0"/>'
        )
        lines.append("  </body>")
    lines.append("</mujocoinclude>")
    return "\n".join(lines)


def gen_objects() -> str:
    cells = free_grid_cells(spacing=0.55, margin=0.4)
    rng = np.random.default_rng(0)
    rng.shuffle(cells)
    n_stacked_pairs = 10
    n_standalone_pallets = N_PALLETS - n_stacked_pairs
    n_standalone_cases = N_CASES - 2 * n_stacked_pairs
    needed = n_stacked_pairs + n_standalone_pallets + n_standalone_cases
    if len(cells) < needed:
        raise RuntimeError(f"配置マス不足: 必要 {needed} 個、空き {len(cells)} 個")

    lines = ["<mujocoinclude>"]
    cell_iter = iter(cells)

    pallet_id = 1
    case_id = 1
    for _ in range(n_stacked_pairs):
        x, y = next(cell_iter)
        pz = PALLET_HALF[2]
        lines.append(f'  <body name="pallet:{pallet_id}" pos="{x} {y} {pz}">')
        lines.append("    <freejoint/>")
        lines.append(
            f'    <geom name="pallet:{pallet_id}" type="box" '
            f'size="{PALLET_HALF[0]} {PALLET_HALF[1]} {PALLET_HALF[2]}" '
            'rgba="0.65 0.5 0.3 1" mass="15"/>'
        )
        lines.append("  </body>")
        case_z1 = 2 * PALLET_HALF[2] + CASE_HALF[2]
        lines.append(f'  <body name="case:{case_id}" pos="{x} {y} {case_z1}">')
        lines.append("    <freejoint/>")
        lines.append(
            f'    <geom name="case:{case_id}" type="box" '
            f'size="{CASE_HALF[0]} {CASE_HALF[1]} {CASE_HALF[2]}" '
            'rgba="0.8 0.7 0.4 1" mass="3"/>'
        )
        lines.append("  </body>")
        case_id += 1
        case_z2 = case_z1 + 2 * CASE_HALF[2]
        lines.append(f'  <body name="case:{case_id}" pos="{x} {y} {case_z2}">')
        lines.append("    <freejoint/>")
        lines.append(
            f'    <geom name="case:{case_id}" type="box" '
            f'size="{CASE_HALF[0]} {CASE_HALF[1]} {CASE_HALF[2]}" '
            'rgba="0.8 0.7 0.4 1" mass="3"/>'
        )
        lines.append("  </body>")
        case_id += 1
        pallet_id += 1

    for _ in range(n_standalone_pallets):
        x, y = next(cell_iter)
        pz = PALLET_HALF[2]
        lines.append(f'  <body name="pallet:{pallet_id}" pos="{x} {y} {pz}">')
        lines.append("    <freejoint/>")
        lines.append(
            f'    <geom name="pallet:{pallet_id}" type="box" '
            f'size="{PALLET_HALF[0]} {PALLET_HALF[1]} {PALLET_HALF[2]}" '
            'rgba="0.65 0.5 0.3 1" mass="15"/>'
        )
        lines.append("  </body>")
        pallet_id += 1

    for _ in range(n_standalone_cases):
        x, y = next(cell_iter)
        cz = CASE_HALF[2]
        lines.append(f'  <body name="case:{case_id}" pos="{x} {y} {cz}">')
        lines.append("    <freejoint/>")
        lines.append(
            f'    <geom name="case:{case_id}" type="box" '
            f'size="{CASE_HALF[0]} {CASE_HALF[1]} {CASE_HALF[2]}" '
            'rgba="0.8 0.7 0.4 1" mass="3"/>'
        )
        lines.append("  </body>")
        case_id += 1

    lines.append("</mujocoinclude>")
    return "\n".join(lines)


def gen_cameras() -> str:
    lines = ["<mujocoinclude>"]
    for cam_id, pos in CAMERAS:
        quat = look_at_quat(np.array(pos), CAMERA_TARGET)
        lines.append(
            f'  <camera name="cam:{cam_id}" pos="{pos[0]} {pos[1]} {pos[2]}" quat="{quat}" '
            f'fovy="{CAMERA_FOVY}"/>'
        )
    lines.append("</mujocoinclude>")
    return "\n".join(lines)


def gen_warehouse() -> str:
    return f"""<mujoco model="gtwm_warehouse">
  <compiler angle="radian" autolimits="true"/>
  <option timestep="0.005" gravity="0 0 -9.81" integrator="implicitfast"/>
  <visual>
    <headlight ambient="0.4 0.4 0.4" diffuse="0.6 0.6 0.6"/>
  </visual>

  <default>
    <geom friction="0.8 0.02 0.001"/>
  </default>

  <asset>
    <texture type="2d" name="floor_tex" builtin="checker" rgb1="0.75 0.75 0.75"
      rgb2="0.65 0.65 0.65" width="128" height="128"/>
    <material name="floor_mat" texture="floor_tex" texrepeat="12 8" reflectance="0.0"/>
  </asset>

  <worldbody>
    <light name="light_main" pos="0 0 6" dir="0 0 -1" diffuse="0.8 0.8 0.8" directional="true"/>
    <geom name="floor" type="plane" size="{FLOOR_HALF_X} {FLOOR_HALF_Y} 0.05" material="floor_mat"/>

    <include file="include/zones.xml"/>
    <include file="include/racks.xml"/>
    <include file="include/docks.xml"/>
    <include file="include/conveyor.xml"/>
    <include file="include/agvs.xml"/>
    <include file="include/workers.xml"/>
    <include file="include/objects.xml"/>
    <include file="include/cameras.xml"/>
  </worldbody>

  <actuator>
    <include file="include/agv_actuators.xml"/>
  </actuator>
</mujoco>
"""


def gen_registry() -> dict:
    def pad(i: int) -> str:
        return f"{i:04d}"

    registry: dict[str, dict[str, str]] = {
        "zones": {},
        "gates": {},
        "docks": {},
        "racks": {},
        "slots": {},
        "equipment": {},
        "agvs": {},
        "workers": {},
        "pallets": {},
        "cases": {},
        "cameras": {},
    }

    for name, _xc in ZONES:
        registry["zones"][f"zone:{name}"] = f"gt:Zone_{name}"
    for gate_id, _x in GATES:
        registry["gates"][f"gate:{gate_id}"] = f"gt:Equipment_Gate_{pad(int(gate_id))}"
    for dock_id, _x, _zone in DOCKS:
        registry["docks"][f"dock:{dock_id}"] = f"gt:Equipment_Dock_{pad(int(dock_id))}"
    for rack_id, _rx, _ry in RACKS:
        registry["racks"][f"rack:{rack_id}"] = f"gt:Equipment_Rack_{pad(int(rack_id))}"
        for level_i in range(len(RACK_LEVELS)):
            for bay_i in range(len(RACK_BAYS)):
                key = f"slot:{rack_id}-{level_i}-{bay_i}"
                registry["slots"][key] = f"gt:Slot_{rack_id}_{level_i}_{bay_i}"
    registry["equipment"]["equipment:conveyor_1"] = "gt:Equipment_Conveyor_0001"
    registry["equipment"]["scale:1"] = "gt:Equipment_Scale_0001"
    for i in range(1, len(AGV_START) + 1):
        registry["agvs"][f"agv:{i}"] = f"gt:Vehicle_{pad(i)}"
    for i in range(1, len(WORKER_START) + 1):
        registry["workers"][f"worker:{i}"] = f"gt:Worker_{pad(i)}"
    for i in range(1, N_PALLETS + 1):
        registry["pallets"][f"pallet:{i}"] = f"gt:Pallet_{pad(i)}"
    for i in range(1, N_CASES + 1):
        registry["cases"][f"case:{i}"] = f"gt:Case_{pad(i)}"
    for cam_id, _pos in CAMERAS:
        registry["cameras"][f"cam:{cam_id}"] = f"gt:Equipment_Camera_{pad(int(cam_id))}"
    return registry


def main() -> None:
    INCLUDE_DIR.mkdir(parents=True, exist_ok=True)
    (INCLUDE_DIR / "zones.xml").write_text(gen_zones() + "\n")
    (INCLUDE_DIR / "racks.xml").write_text(gen_racks() + "\n")
    (INCLUDE_DIR / "docks.xml").write_text(gen_docks() + "\n")
    (INCLUDE_DIR / "conveyor.xml").write_text(gen_conveyor() + "\n")
    (INCLUDE_DIR / "agvs.xml").write_text(gen_agvs() + "\n")
    (INCLUDE_DIR / "agv_actuators.xml").write_text(gen_agv_actuators() + "\n")
    (INCLUDE_DIR / "workers.xml").write_text(gen_workers() + "\n")
    (INCLUDE_DIR / "objects.xml").write_text(gen_objects() + "\n")
    (INCLUDE_DIR / "cameras.xml").write_text(gen_cameras() + "\n")
    (ASSETS_DIR / "warehouse.xml").write_text(gen_warehouse())
    with (ASSETS_DIR / "registry.yaml").open("w") as f:
        yaml.safe_dump(gen_registry(), f, allow_unicode=True, sort_keys=True)
    print("generated warehouse.xml, include/*.xml, registry.yaml")


if __name__ == "__main__":
    main()
