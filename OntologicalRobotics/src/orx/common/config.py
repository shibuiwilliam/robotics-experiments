"""コンフィグローダ（YAML→pydantic）と構成ハッシュ。

全実験はコンフィグ駆動（CLAUDE.md §6）。出力には構成ハッシュ・シード・
モデルスナップショット名を焼き込む（PROJECT.md §8.3）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, TypeVar

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
    weight_kg: float = 0.5  # 把持の成否に効く（T3/T6, H3）
    material: str = "cardboard"  # 素材タグ（reflective/glass等は把持difficulty）
    lot: str | None = None  # ロット番号（S1/T8。業務lotテーブルの種。simは使わない）


class CameraConfig(StrictModel):
    pos: Vec3
    lookat: Vec3
    fovy: float = 60.0


class SkillTruthProfile(StrictModel):
    """故障注入の真値パラメータ（C2のみが解釈。台帳推定の正解, T6）。"""

    max_payload_kg: float = 5.0  # 超過時は把持がほぼ失敗する
    reach_m: float = 3.0  # ベース（カメラ位置）からの到達半径
    base_success: float = 0.95
    material_success: dict[str, float] = Field(default_factory=dict)  # 素材別成功率
    overload_success: float = 0.10  # 重量超過時の成功率


class CapabilityDeclaration(StrictModel):
    """ロボットが宣言する能力契約（身体化APIスキーマ）。真値と乖離しうる。"""

    skills: list[str] = Field(default_factory=lambda: ["pick", "place"])
    declared_payload_kg: float = 5.0
    declared_reach_m: float = 3.0
    prior_success: float = 0.9  # 経験前の宣言成功率（台帳が較正していく）


class RobotConfig(StrictModel):
    name: str
    vendor_schema: str  # ontology/mappings/ のマッピング名と一致
    camera: CameraConfig
    detection_range: float = 4.0
    barcode_read_range: float = 2.5  # 0.0 = 記号ID読取不可（擬似LiDAR等）
    visual_embedding: bool = True  # False = 埋め込み無し（LiDAR系センサ）
    skill_truth: SkillTruthProfile | None = None  # None = 操作スキル無し
    capability: CapabilityDeclaration | None = None


class ScriptedMove(StrictModel):
    """評価用の状態遷移イベント。

    mode="teleport": at_time に瞬間移動（P0互換）。
    mode="slide": at_time から duration_s かけて等速で移動（時空間連続性が保たれ、
    アンカリングの動きゲートで追跡可能 — T1の搬送イベントに使う）。
    """

    box: str
    at_time: float
    to_zone: str
    mode: Literal["teleport", "slide"] = "teleport"
    duration_s: float = 0.0
    offset: tuple[float, float] = (0.0, 0.0)  # ゾーン中心からのxyオフセット


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
    """C5 アンカリングのパラメータ（D1: 2仮説スコア、重み・閾値はコンフィグ駆動）。

    空間スコアは「静止仮説」（時間で広がるガウス、密度正規化で大σにペナルティ）と
    「搬送仮説」（運動コーン内の定常尤度、休眠時間と共に立ち上がる）の最大値。
    埋め込みコサイン類似は乗法的に変調する（双対表現, H4）。
    """

    spatial_sigma: float = 0.06  # 静止仮説の基底σ [m]（センサノイズより十分大きく）
    sigma_growth_tau: float = 2.0  # σ成長の時定数 [s]（σ_eff = σ·(1+Δt/τ)）
    transit_score: float = 0.50  # 搬送仮説の上限スコア
    transit_sigma_base: float = 0.20  # 等速予測の残差σ基底 [m]
    transit_sigma_rate: float = 0.10  # 予測誤差の成長率 [m/s]
    v_max: float = 0.75  # 速度推定のクランプ [m/s]
    velocity_ema: float = 0.5  # 速度推定の指数移動平均係数
    w_embedding: float = 0.4  # 埋め込み変調の強さ（factor = 1-w + w·cos）
    theta_merge: float = 0.10  # マッチ採用の最低スコア
    id_confidence: float = 0.98  # 記号ID一致時の確信度
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
    belief_enabled: bool = True  # False = OR−belief アブレーション (H5)
