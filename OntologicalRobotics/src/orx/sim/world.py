"""SimWorld — MuJoCo世界の生成・ステップ・真値公開・オフスクリーン描画。

不変条件4: 物理ステップの内側ループでグラフクエリ・LLM・ディスクI/Oを行わない。
このクラスは純粋に物理と描画のみを扱う。
"""

from __future__ import annotations

import mujoco
import numpy as np

from orx.common.config import ScriptedMove, WorldConfig
from orx.common.geometry import zone_of
from orx.common.schemas import TruthObject, TruthState, Vec3
from orx.common.seeding import SeedTree
from orx.sim.mjcf import body_name, build_mjcf, camera_name, joint_name

RENDER_SIZE = 224  # CLIP入力に整合 (PROJECT.md §10)


class CameraView:
    """描画時点のカメラ姿勢と内部パラメータ（知覚の射影に使う）。"""

    def __init__(self, pos: np.ndarray, xmat: np.ndarray, fovy_deg: float, size: int) -> None:
        self.pos = pos
        self.xmat = xmat  # 3x3, 列がカメラ軸 (MuJoCo: -Z が視線)
        self.fovy_deg = fovy_deg
        self.size = size


class SimWorld:
    def __init__(self, config: WorldConfig, seeds: SeedTree) -> None:
        self.config = config
        xml = build_mjcf(config, seeds.child("placement").rng())
        self.model = mujoco.MjModel.from_xml_string(xml)
        self.data = mujoco.MjData(self.model)
        mujoco.mj_forward(self.model, self.data)
        self._renderer: mujoco.Renderer | None = None  # 生成は1回 (CLAUDE.md §9)
        self._pending_moves: list[ScriptedMove] = sorted(
            config.scripted_moves, key=lambda m: m.at_time
        )
        self._active_slides: list[
            tuple[ScriptedMove, tuple[float, ...], tuple[float, ...]]
        ] = []
        self._zones = {z.name: z for z in config.zones}

    # ------------------------------------------------------------------ time

    @property
    def sim_time(self) -> float:
        return float(self.data.time)

    def step_to(self, target_time: float) -> None:
        """target_time まで物理を進める。スクリプト移動は期日に適用する。"""
        while self.data.time < target_time - 1e-9:
            while self._pending_moves and self._pending_moves[0].at_time <= self.data.time:
                self._start_move(self._pending_moves.pop(0))
            self._advance_slides()
            mujoco.mj_step(self.model, self.data)
        self._advance_slides()

    def _move_target(self, move: ScriptedMove) -> tuple[float, float, float]:
        zone = self._zones.get(move.to_zone)
        if zone is None:
            raise ValueError(f"scripted move {move.box!r}: 未定義ゾーン {move.to_zone!r}")
        box = next(b for b in self.config.boxes if b.name == move.box)
        return (
            zone.center[0] + move.offset[0],
            zone.center[1] + move.offset[1],
            zone.center[2] - zone.size[2] / 2 + box.size + 0.002,
        )

    def _set_box_pose(self, box_name: str, pos: tuple[float, float, float]) -> None:
        jnt = self.model.joint(joint_name(box_name))
        adr = jnt.qposadr[0]
        self.data.qpos[adr : adr + 3] = pos
        self.data.qpos[adr + 3 : adr + 7] = [1.0, 0.0, 0.0, 0.0]
        dofadr = jnt.dofadr[0]
        self.data.qvel[dofadr : dofadr + 6] = 0.0

    def _start_move(self, move: ScriptedMove) -> None:
        dest = self._move_target(move)
        if move.mode == "teleport" or move.duration_s <= 0:
            self._set_box_pose(move.box, dest)
            mujoco.mj_forward(self.model, self.data)
            return
        start = tuple(float(v) for v in self.data.body(body_name(move.box)).xpos)
        self._active_slides.append((move, start, dest))

    _SLIDE_LIFT = 0.35  # 搬送アークの最大持ち上げ [m]（他の箱を薙ぎ倒さない）

    def _advance_slides(self) -> None:
        if not self._active_slides:
            return
        now = float(self.data.time)
        remaining: list[tuple[ScriptedMove, tuple[float, ...], tuple[float, ...]]] = []
        for move, start, dest in self._active_slides:
            alpha = min(1.0, (now - move.at_time) / move.duration_s)
            # クレーン状の3相: 垂直上昇(15%) → 水平移動(70%) → 垂直降下(15%)。
            # 隣接箱を横移動で薙ぎ倒さないために相を分離する。
            a_up = min(1.0, alpha / 0.15)
            a_move = min(1.0, max(0.0, (alpha - 0.15) / 0.70))
            a_down = min(1.0, max(0.0, (alpha - 0.85) / 0.15))
            x = start[0] + a_move * (dest[0] - start[0])
            y = start[1] + a_move * (dest[1] - start[1])
            z = start[2] + a_move * (dest[2] - start[2]) + self._SLIDE_LIFT * (a_up - a_down)
            self._set_box_pose(move.box, (x, y, z))
            if alpha < 1.0:
                remaining.append((move, start, dest))
        self._active_slides = remaining
        mujoco.mj_forward(self.model, self.data)

    # ----------------------------------------------------------------- truth

    def truth(self) -> TruthState:
        """完全真値状態。**oracle と採点部のみが消費してよい。**"""
        objects: list[TruthObject] = []
        for box in self.config.boxes:
            pos = self.data.body(body_name(box.name)).xpos
            position: Vec3 = (float(pos[0]), float(pos[1]), float(pos[2]))
            objects.append(
                TruthObject(
                    object_id=box.name,
                    position=position,
                    barcode=box.barcode,
                    zone=zone_of(self.config.zones, position),
                )
            )
        return TruthState(sim_time=self.sim_time, objects=objects)

    # ---------------------------------------------------------------- render

    def render(self, robot_name: str) -> tuple[np.ndarray, CameraView]:
        if self._renderer is None:
            self._renderer = mujoco.Renderer(self.model, RENDER_SIZE, RENDER_SIZE)
        cam = camera_name(robot_name)
        self._renderer.update_scene(self.data, camera=cam)
        image = self._renderer.render()
        cam_id = self.model.camera(cam).id
        view = CameraView(
            pos=np.array(self.data.cam_xpos[cam_id]),
            xmat=np.array(self.data.cam_xmat[cam_id]).reshape(3, 3),
            fovy_deg=float(self.model.cam_fovy[cam_id]),
            size=RENDER_SIZE,
        )
        return image, view

    def camera_view(self, robot_name: str) -> CameraView:
        cam_id = self.model.camera(camera_name(robot_name)).id
        return CameraView(
            pos=np.array(self.data.cam_xpos[cam_id]),
            xmat=np.array(self.data.cam_xmat[cam_id]).reshape(3, 3),
            fovy_deg=float(self.model.cam_fovy[cam_id]),
            size=RENDER_SIZE,
        )

    def close(self) -> None:
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None
