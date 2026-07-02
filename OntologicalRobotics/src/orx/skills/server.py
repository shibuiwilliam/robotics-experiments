"""C2 — 擬似VLAスキルサーバ（pick/place）＋条件付き故障注入。

言語指示風のリクエストを受け、パラメータ化スクリプトで実行する。
故障注入の真値（SkillTruthProfile）は**このモジュールだけが解釈する**。
計画側（C7/exp）は能力台帳の推定だけを見る — 真の注入率を知るのは
oracle/採点部のみ（T6で台帳の較正を測る）。
"""

from __future__ import annotations

import numpy as np

from orx.common.config import RobotConfig, WorldConfig
from orx.common.schemas import StrictModel, Vec3


class SkillRequest(StrictModel):
    """『箱 {barcode} をゾーン {dest_zone} へ』という指示の構造化形。"""

    robot_id: str
    skill: str  # "pick_and_place"
    target_barcode: str
    target_position: Vec3  # 計画側が世界グラフから得た位置
    dest_zone: str


class SkillOutcome(StrictModel):
    request: SkillRequest
    success: bool
    failure_mode: str | None = None  # out_of_reach / overload / grip_slip
    sim_time: float = 0.0


class SkillServer:
    """スクリプトスキル＋故障注入器（成否と故障モードを返す）。

    P3では結果（成否）が被験変数であり、物理的な箱の移動は接続しない
    （実VLA差し替え時に接続する — PROJECT.md P5任意項目）。
    """

    def __init__(self, world_config: WorldConfig, rng: np.random.Generator) -> None:
        self.config = world_config
        self._rng = rng
        self._robots: dict[str, RobotConfig] = {r.name: r for r in world_config.robots}
        self._boxes = {b.barcode: b for b in world_config.boxes if b.barcode}

    def true_success_rate(self, robot_id: str, barcode: str) -> float:
        """真の成功率（**採点・較正の正解にのみ使う**。計画側は呼ばない）。"""
        robot = self._robots[robot_id]
        profile = robot.skill_truth
        if profile is None:
            return 0.0
        box = self._boxes.get(barcode)
        if box is None:
            return 0.0
        d = (
            sum(
                (a - b) ** 2
                for a, b in zip(robot.camera.pos, self._box_position(barcode), strict=True)
            )
            ** 0.5
        )
        if d > profile.reach_m:
            return 0.0
        if box.weight_kg > profile.max_payload_kg:
            return profile.overload_success
        return profile.material_success.get(box.material, profile.base_success)

    def _box_position(self, barcode: str) -> Vec3:
        # 静的世界の初期ゾーン中心で近似（実行はゾーン単位の運搬）
        box = self._boxes[barcode]
        zone = next(z for z in self.config.zones if z.name == box.zone)
        return zone.center

    def execute(self, request: SkillRequest, sim_time: float = 0.0) -> SkillOutcome:
        robot = self._robots.get(request.robot_id)
        if robot is None or robot.skill_truth is None:
            return SkillOutcome(
                request=request, success=False, failure_mode="no_skill", sim_time=sim_time
            )
        profile = robot.skill_truth
        box = self._boxes.get(request.target_barcode)
        if box is None:
            return SkillOutcome(
                request=request,
                success=False,
                failure_mode="unknown_target",
                sim_time=sim_time,
            )
        d = (
            sum(
                (a - b) ** 2 for a, b in zip(robot.camera.pos, request.target_position, strict=True)
            )
            ** 0.5
        )
        if d > profile.reach_m:
            return SkillOutcome(
                request=request,
                success=False,
                failure_mode="out_of_reach",
                sim_time=sim_time,
            )
        if box.weight_kg > profile.max_payload_kg:
            success = bool(self._rng.random() < profile.overload_success)
            return SkillOutcome(
                request=request,
                success=success,
                failure_mode=None if success else "overload",
                sim_time=sim_time,
            )
        rate = profile.material_success.get(box.material, profile.base_success)
        success = bool(self._rng.random() < rate)
        return SkillOutcome(
            request=request,
            success=success,
            failure_mode=None if success else "grip_slip",
            sim_time=sim_time,
        )
