"""コンフィグローダ（YAML→pydantic）と構成ハッシュ。

全実験はコンフィグ駆動（CLAUDE.md §6）。出力には構成ハッシュ・シード・
モデルスナップショット名を焼き込む（PROJECT.md §8.3）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, Field, ValidationError

from orx.common.providers import ProviderConfig
from orx.common.schemas import StrictModel, Vec3

M = TypeVar("M", bound=BaseModel)


class ConfigError(ValueError):
    """コンフィグ読込・検証エラー（CLIで実行可能なメッセージに変換する）。"""


def load_config(path: Path, model: type[M]) -> M:
    if not path.exists():
        raise ConfigError(f"コンフィグが見つかりません: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML解析エラー ({path}): {exc}") from exc
    try:
        return model.model_validate(data or {})
    except ValidationError as exc:
        raise ConfigError(f"コンフィグ検証エラー ({path}):\n{exc}") from exc


def config_hash(model: BaseModel) -> str:
    raw = json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# --------------------------------------------------------------- world config


class ZoneConfig(StrictModel):
    """名前付きゾーン（軸平行な箱領域）。inZone離散化の単位。"""

    name: str
    center: Vec3
    size: Vec3  # 半径ではなく全幅


class BoxConfig(StrictModel):
    name: str
    zone: str  # 初期配置ゾーン
    size: float = 0.06  # 立方体半辺 [m]
    rgba: tuple[float, float, float, float] = (0.8, 0.6, 0.3, 1.0)
    barcode: str | None = None  # 記号識別子（無しはID無し個体）


class CameraConfig(StrictModel):
    pos: Vec3
    lookat: Vec3
    fovy: float = 60.0


class RobotConfig(StrictModel):
    name: str
    vendor_schema: str  # ontology/mappings/ のマッピング名と一致
    camera: CameraConfig
    detection_range: float = 4.0
    barcode_read_range: float = 2.5


class ScriptedMove(StrictModel):
    """評価用の状態遷移イベント: at_time に box を to_zone へ移す。"""

    box: str
    at_time: float
    to_zone: str


class DegradationConfig(StrictModel):
    """劣化ノブ（PROJECT.md §7.3）。sim/perception側のみが解釈する。"""

    id_read_failure_rate: float = 0.0
    pose_noise_sigma: float = 0.0
    occlusion_rate: float = 0.0
    observation_delay_s: float = 0.0
    contradiction_rate: float = 0.0


class WorldConfig(StrictModel):
    name: str
    physics_dt: float = 0.002  # 500Hz
    perception_hz: float = 2.0
    eval_hz: float = 1.0
    settle_s: float = 1.0  # 物理静定時間（知覚開始前）
    zones: list[ZoneConfig]
    boxes: list[BoxConfig]
    robots: list[RobotConfig]
    scripted_moves: list[ScriptedMove] = Field(default_factory=list)
    degradation: DegradationConfig = Field(default_factory=DegradationConfig)


class AnchoringParams(StrictModel):
    """C5 アンカリングのパラメータ（D1: 重み・閾値はコンフィグ駆動）。"""

    gate_radius: float = 0.30  # 時空間ゲート [m]
    spatial_sigma: float = 0.15  # 空間スコアの尺度 [m]
    id_confidence: float = 0.98  # 記号ID一致時の確信度
    new_entity_confidence: float = 0.90
    enabled: bool = True  # False = OR−identity アブレーション（検出毎に新個体）


class RunConfig(StrictModel):
    """1記録セッションの設定（sim run / demo が使用）。"""

    world: WorldConfig
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    anchoring: AnchoringParams = Field(default_factory=AnchoringParams)
    visual_embedder: str = "stub"  # "stub" | "clip"
    duration_s: float = 20.0
    root_seed: int = 7
    claim_ttl_s: float = 5.0  # 観測由来主張の有効期間
