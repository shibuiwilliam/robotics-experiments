"""MuJoCo 倉庫環境：ステッピングと 10Hz ログ用の状態取得。

物理タイムステップは `sim/assets/warehouse.xml` の `<option timestep="0.005">` が真値。
アンカー検出（`sensors/anchors.py`）はこのモジュールが返す軌跡データを後段で読むだけで、
物理コールバックの中では判定しない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import mujoco
import numpy as np

from gtwm.sim.paths import AgvController, WorkerPath, make_worker_path
from gtwm.utils.paths import warehouse_xml_path

PHYSICS_DT = 0.005
N_PALLETS = 20
N_CASES = 40

ZONE_BOUNDS = [-6.0, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0]
ZONE_NAMES = ["Dock_In", "Inspect", "Storage_A", "Storage_B", "Pick", "Dock_Out"]

# AGV は倉庫の外周レーン（棚と干渉しない y=±3.5）を巡回する簡易 P 制御。
AGV_LOOPS: dict[str, list[tuple[float, float]]] = {
    "agv:1": [(-5.0, -3.5), (-3.0, -3.5), (0.0, -3.5), (3.0, -3.5), (5.0, -3.5)],
    "agv:2": [(5.0, 3.5), (3.0, 3.5), (0.0, 3.5), (-3.0, 3.5), (-5.0, 3.5)],
}
# 作業者はカプセルのキネマティック移動（スプライン経路）。数値はゾーン間を巡回するよう選ぶ。
WORKER_LOOPS: dict[str, list[tuple[float, float]]] = {
    "worker:1": [(-3.0, 3.0), (-1.0, 3.5), (1.0, 3.5), (3.0, 3.0)],
    "worker:2": [(-4.0, -3.0), (-2.0, -3.5), (0.0, -3.0), (2.0, -3.5)],
    "worker:3": [(-2.0, 0.5), (0.0, -0.8), (2.0, 0.5), (0.0, 1.8)],
}
WORKER_LOOP_SECONDS = 20.0


def zone_of_x(x: float) -> str:
    idx = int(np.clip(np.searchsorted(ZONE_BOUNDS, x) - 1, 0, len(ZONE_NAMES) - 1))
    return ZONE_NAMES[idx]


@dataclass
class RawTrajectory:
    """1エピソードぶんの真値軌跡（10Hz）。"""

    times: np.ndarray  # [T]
    positions: dict[str, np.ndarray] = field(default_factory=dict)  # name -> [T,3]
    yaws: dict[str, np.ndarray] = field(default_factory=dict)  # name -> [T]（AGV のみ意味を持つ）
    zones: dict[str, list[str]] = field(default_factory=dict)  # name -> [T]


class WarehouseEnv:
    """倉庫 MJCF を読み込み、AGV/作業者の簡易スクリプト制御でステップする。"""

    def __init__(self, seed: int = 0) -> None:
        self.model = mujoco.MjModel.from_xml_path(str(warehouse_xml_path()))
        self.data = mujoco.MjData(self.model)
        self.seed = seed

        self.pallet_names = [f"pallet:{i}" for i in range(1, N_PALLETS + 1)]
        self.case_names = [f"case:{i}" for i in range(1, N_CASES + 1)]
        self.agv_names = list(AGV_LOOPS.keys())
        self.worker_names = list(WORKER_LOOPS.keys())
        self.tracked_names = (
            self.pallet_names + self.case_names + self.agv_names + self.worker_names
        )
        self.occlusion_names = self.pallet_names + self.case_names + self.agv_names

        self.body_ids = {
            name: mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
            for name in self.tracked_names
        }
        self.geom_ids = {
            name: mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, name)
            for name in self.occlusion_names
        }
        self.mocap_ids = {
            name: self.model.body_mocapid[self.body_ids[name]] for name in self.worker_names
        }
        self.cam_ids = {
            f"cam:{i}": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, f"cam:{i}")
            for i in range(1, 5)
        }

        self.agv_controllers = {
            name: AgvController(waypoints=np.array(wp)) for name, wp in AGV_LOOPS.items()
        }
        self.worker_paths: dict[str, WorkerPath] = {
            name: make_worker_path(
                wp, speed=len(wp) / WORKER_LOOP_SECONDS, phase=float(i), seed=seed * 100 + i
            )
            for i, (name, wp) in enumerate(WORKER_LOOPS.items())
        }
        self.agv_ctrl_ids = {
            name: [
                mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{name}_x_act"),
                mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{name}_y_act"),
                mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{name}_yaw_act"),
            ]
            for name in self.agv_names
        }

        mujoco.mj_forward(self.model, self.data)

    def _apply_controls(self, t_s: float) -> None:
        for name, ctrl in self.agv_controllers.items():
            body_id = self.body_ids[name]
            pos_xy = np.array(self.data.xpos[body_id][:2])
            rot = np.array(self.data.xmat[body_id]).reshape(3, 3)
            yaw = math.atan2(rot[1, 0], rot[0, 0])
            vx, vy, vyaw = ctrl.command(pos_xy, yaw)
            act_x, act_y, act_yaw = self.agv_ctrl_ids[name]
            self.data.ctrl[act_x] = vx
            self.data.ctrl[act_y] = vy
            self.data.ctrl[act_yaw] = vyaw

        for name, path in self.worker_paths.items():
            xy = path.position_at(t_s)
            mocap_id = self.mocap_ids[name]
            self.data.mocap_pos[mocap_id] = [float(xy[0]), float(xy[1]), 0.5]

    def advance_log_tick(self, t_s: float, substeps: int) -> None:
        """次の 1/log_hz 秒ぶんの制御を適用し、物理を substeps 回進める。"""
        self._apply_controls(t_s)
        for _ in range(substeps):
            mujoco.mj_step(self.model, self.data)

    def snapshot(self) -> dict[str, tuple[np.ndarray, float, str]]:
        """現在時刻の各追跡対象の (位置, yaw, ゾーン) を返す。"""
        result: dict[str, tuple[np.ndarray, float, str]] = {}
        for name in self.tracked_names:
            body_id = self.body_ids[name]
            pos = np.array(self.data.xpos[body_id])
            if name in self.agv_names:
                rot = np.array(self.data.xmat[body_id]).reshape(3, 3)
                yaw = math.atan2(rot[1, 0], rot[0, 0])
            else:
                yaw = 0.0
            zone = zone_of_x(float(pos[0]))
            result[name] = (pos, yaw, zone)
        return result
