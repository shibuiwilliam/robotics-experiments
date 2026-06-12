"""合成検出器とベンダースキーマ観測エミッタ。

検出器はシム状態を読む「センサモデル」であり、劣化ノブ（PROJECT.md §7.3）は
ここ（sim側）にのみ実装する。anchoring/kg がノイズを知ることは禁止
（CLAUDE.md §9）。

ベンダースキーマA (`vendor_arm_a`): ネストJSON・英語略記・m単位。
このスキーマは**意図的に異質**であり、正規化は ontology/mappings/ の仕事。
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from orx.common.config import DegradationConfig, RobotConfig
from orx.common.schemas import RawObservation
from orx.sim.mjcf import body_name
from orx.sim.world import SimWorld


class SensedObject:
    """センサ内部表現（ベンダー形式に直す前）。"""

    def __init__(
        self,
        true_object_id: str,
        position: tuple[float, float, float],
        barcode: str | None,
        confidence: float,
    ) -> None:
        self.true_object_id = true_object_id
        self.position = position
        self.barcode = barcode
        self.confidence = confidence


def _visible(
    world: SimWorld, robot: RobotConfig, cam_pos: np.ndarray, cam_xmat: np.ndarray,
    target_pos: np.ndarray, target_body_id: int, robot_body_id: int,
) -> bool:
    """視錐台内かつ遮蔽されていないか（mj_ray による遮蔽判定）。"""
    rel = target_pos - cam_pos
    dist = float(np.linalg.norm(rel))
    if dist < 1e-6 or dist > robot.detection_range:
        return False
    p_cam = cam_xmat.T @ rel  # カメラフレーム（-Z が視線）
    if p_cam[2] > -1e-6:
        return False
    half_fov = math.radians(robot.camera.fovy) / 2
    limit = math.tan(half_fov)
    if abs(p_cam[0] / -p_cam[2]) > limit or abs(p_cam[1] / -p_cam[2]) > limit:
        return False
    direction = rel / dist
    geomid = np.zeros(1, dtype=np.int32)
    mujoco.mj_ray(
        world.model, world.data, cam_pos, direction,
        None, 1, robot_body_id, geomid,  # 自分の身体は遮蔽判定から除外
    )
    if geomid[0] < 0:
        return False
    hit_body = int(world.model.geom_bodyid[geomid[0]])
    return hit_body == target_body_id


def sense(
    world: SimWorld,
    robot: RobotConfig,
    knobs: DegradationConfig,
    rng: np.random.Generator,
) -> list[SensedObject]:
    """ロボットのカメラから見える箱を検出する（劣化ノブ適用済み）。

    contradiction_rate: 確率pで検出位置が「初期配置のステイルキャッシュ」に
    化ける（品質スコアは低下する — 現実のセンサ異常は品質指標と相関する）。
    observation_delay_s はセンサではなく配信の遅延なので、パイプライン側
    （orx.exp.episode の遅延キュー）で適用される。
    """
    view = world.camera_view(robot.name)
    robot_body_id = world.model.body(f"robot_{robot.name}").id
    sensed: list[SensedObject] = []
    for box in world.config.boxes:
        body_id = world.model.body(body_name(box.name)).id
        true_pos = np.array(world.data.body(body_name(box.name)).xpos)
        if not _visible(world, robot, view.pos, view.xmat, true_pos, body_id, robot_body_id):
            continue
        if knobs.occlusion_rate > 0 and rng.random() < knobs.occlusion_rate:
            continue
        noisy = true_pos + rng.normal(0.0, knobs.pose_noise_sigma, 3) \
            if knobs.pose_noise_sigma > 0 else true_pos
        dist = float(np.linalg.norm(true_pos - view.pos))
        barcode: str | None = None
        if box.barcode is not None and dist <= robot.barcode_read_range:
            if not (knobs.id_read_failure_rate > 0 and rng.random() < knobs.id_read_failure_rate):
                barcode = box.barcode
        confidence = max(0.5, 1.0 - 0.05 * dist / max(robot.detection_range, 1e-6))
        sensed.append(
            SensedObject(
                true_object_id=box.name,
                position=(float(noisy[0]), float(noisy[1]), float(noisy[2])),
                barcode=barcode,
                confidence=round(confidence, 4),
            )
        )
    if knobs.contradiction_rate > 0:
        sensed.extend(_stale_cache_ghosts(world, robot, knobs, rng, sensed))
    return sensed


def _stale_cache_ghosts(
    world: SimWorld,
    robot: RobotConfig,
    knobs: DegradationConfig,
    rng: np.random.Generator,
    sensed: list[SensedObject],
) -> list[SensedObject]:
    """矛盾観測: ステイルなトラックキャッシュの再送出。

    かつて読めた（初期位置がID読取圏内の）バーコード箱が現在見えていないとき、
    確率pで初期位置＋バーコードの「古い記録」を低品質スコアで再送出する。
    同一個体（バーコードで束ねられる）に対する矛盾主張が生まれ、来歴・確信度
    ベースの信念調停の被験条件になる（H5）。
    """
    visible_ids = {s.true_object_id for s in sensed}
    view = world.camera_view(robot.name)
    ghosts: list[SensedObject] = []
    for box in world.config.boxes:
        if box.barcode is None or box.name in visible_ids:
            continue
        initial = world.initial_position(box.name)
        dist0 = float(np.linalg.norm(np.array(initial) - view.pos))
        if dist0 > robot.barcode_read_range:
            continue  # そもそも記録に無い箱は再送出しない
        if rng.random() < knobs.contradiction_rate:
            ghosts.append(
                SensedObject(
                    true_object_id=box.name,
                    position=initial,
                    barcode=box.barcode,
                    confidence=0.60,  # 異常データは品質スコアが下がる
                )
            )
    return ghosts


# ----------------------------------------------------- vendor schema emitters


def emit_vendor_arm_a(
    robot_id: str, sim_time: float, seq: int, sensed: list[SensedObject]
) -> RawObservation:
    """ベンダーA形式: ネスト構造・略記キー・メートル。"""
    payload = {
        "hdr": {"rid": robot_id, "ts": round(sim_time, 6), "n": len(sensed)},
        "dets": [
            {
                "p": {
                    "x": round(s.position[0], 6),
                    "y": round(s.position[1], 6),
                    "z": round(s.position[2], 6),
                },
                "bc": s.barcode,
                "cf": s.confidence,
            }
            for s in sensed
        ],
    }
    return RawObservation(
        robot_id=robot_id,
        vendor_schema="vendor_arm_a",
        sim_time=sim_time,
        seq=seq,
        payload=payload,
        oracle_truth_ids=[s.true_object_id for s in sensed],
    )


def emit_vendor_mobile_b(
    robot_id: str, sim_time: float, seq: int, sensed: list[SensedObject]
) -> RawObservation:
    """ベンダーB形式: フラット構造・別命名規則・センチメートル・ID読取なし。

    擬似LiDAR想定 — 記号識別子フィールド自体が存在しない（意図的異質性）。
    """
    payload = {
        "device_serial": robot_id,
        "stamp_ms": round(sim_time * 1000.0, 3),
        "frame_no": seq,
        "objects": [
            {
                "px_cm": round(s.position[0] * 100.0, 4),
                "py_cm": round(s.position[1] * 100.0, 4),
                "pz_cm": round(s.position[2] * 100.0, 4),
                "quality": s.confidence,
            }
            for s in sensed
        ],
    }
    return RawObservation(
        robot_id=robot_id,
        vendor_schema="vendor_mobile_b",
        sim_time=sim_time,
        seq=seq,
        payload=payload,
        oracle_truth_ids=[s.true_object_id for s in sensed],
    )


_EMITTERS = {"vendor_arm_a": emit_vendor_arm_a, "vendor_mobile_b": emit_vendor_mobile_b}


def observe(
    world: SimWorld,
    robot: RobotConfig,
    seq: int,
    rng: np.random.Generator,
    knobs: DegradationConfig | None = None,
) -> RawObservation:
    """1ロボットの観測を、そのベンダースキーマで発行する。"""
    emitter = _EMITTERS.get(robot.vendor_schema)
    if emitter is None:
        raise ValueError(
            f"未知のベンダースキーマ {robot.vendor_schema!r}"
            f"（対応: {sorted(_EMITTERS)}）"
        )
    effective = knobs if knobs is not None else world.config.degradation
    sensed = sense(world, robot, effective, rng)
    return emitter(robot.name, world.sim_time, seq, sensed)
