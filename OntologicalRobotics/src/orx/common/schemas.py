"""全メッセージ・設定のpydanticモデル起点（CLAUDE.md §4: dict素通し禁止）。

例外: `RawObservation.payload` はベンダースキーマ（意図的に異質・非統一、
PROJECT.md §4.1）であり型付けしない。正規化はリフティング層の仕事。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """共通基底: 未知フィールド拒否・代入時検証。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


Vec3 = tuple[float, float, float]


# ---------------------------------------------------------------- observation


class RawObservation(StrictModel):
    """C1が発するベンダースキーマ観測。payload の形はベンダー毎に異なる。

    `oracle_truth_ids` は payload 内の検出列と添字で整合する真値物体ID。
    **評価(oracle)と採点のみが読んでよい**（anchoring/kg/agent からの参照は
    アーキテクチャテストで禁止）。
    """

    robot_id: str
    vendor_schema: str
    sim_time: float
    seq: int
    payload: dict[str, Any]
    oracle_truth_ids: list[str | None] = Field(default_factory=list)


class Detection(StrictModel):
    """リフティング後の正規化検出（共通単位系: m, 世界座標）。"""

    sensor_id: str
    index: int
    position: Vec3
    symbol_id: str | None = None
    embedding: list[float] | None = None
    confidence: float = 1.0


class PerceptionEvent(StrictModel):
    """C4が発する知覚イベント。oracle_truth_ids は detections と添字整合。"""

    event_id: str
    robot_id: str
    sim_time: float
    detections: list[Detection]
    oracle_truth_ids: list[str | None] = Field(default_factory=list)


# --------------------------------------------------------------------- claims


class Term(StrictModel):
    """RDF項（IRIまたはリテラル）の直列化表現。"""

    kind: Literal["iri", "literal"]
    value: str
    datatype: str | None = None

    def canonical(self) -> str:
        if self.kind == "iri":
            return f"<{self.value}>"
        if self.datatype:
            return f'"{self.value}"^^<{self.datatype}>'
        return f'"{self.value}"'


class Claim(StrictModel):
    """世界グラフへの1主張。来歴・確信度・時刻は必須（PROJECT.md §5.2-2）。"""

    claim_id: str
    subject: str  # IRI
    predicate: str  # IRI
    object: Term
    asserted_by: str  # IRI (orx-prov:Agent)
    confidence: float = Field(ge=0.0, le=1.0)
    observed_at: float  # sim time [s]
    valid_until: float | None = None  # sim time [s]; None = 失効しない


class AnchorRecord(StrictModel):
    """C5の同一性判定1件。entity_iri は世界グラフ個体。"""

    track_id: str
    entity_iri: str
    score: float
    sim_time: float
    decision: Literal["new", "match"]


class AnchorObservation(StrictModel):
    """oracle採点用: 検出→世界個体の対応と、その検出の真値物体ID。"""

    entity_iri: str
    true_object_id: str
    sim_time: float


# ---------------------------------------------------------------------- truth


class TruthObject(StrictModel):
    """シム真値の1物体。**oracle と実験ランナーの採点部のみが消費してよい**。"""

    object_id: str
    position: Vec3
    barcode: str | None
    zone: str | None


class TruthState(StrictModel):
    """評価ティックの完全真値状態（C8 oracle 専用の入力）。"""

    sim_time: float
    objects: list[TruthObject]


# ------------------------------------------------------------------ snapshots


class TripleRecord(StrictModel):
    """忠実度比較用の正規化トリプル。object は Term.canonical() 形式。"""

    subject: str
    predicate: str
    object: str

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.subject, self.predicate, self.object)


class StateSnapshot(StrictModel):
    """ある評価ティックの状態スナップショット（世界グラフ/真理グラフ共通形式）。"""

    sim_time: float
    triples: list[TripleRecord]
    positions: dict[str, Vec3] = Field(default_factory=dict)


class ZoneTransition(StrictModel):
    """ゾーン遷移イベント（真値またはグラフ主張から導出）。"""

    object_id: str
    to_zone: str
    sim_time: float


# -------------------------------------------------------------------- reports


class FidelityReport(StrictModel):
    """忠実度メトリクス（PROJECT.md §7.1）。"""

    n_eval_ticks: int
    triple_precision: float
    triple_recall: float
    triple_f1: float
    identity_precision: float
    identity_recall: float
    identity_f1: float
    position_rmse: float
    transition_mean_delay_s: float | None
    transition_miss_rate: float
    staleness_rate: float


class RunManifest(StrictModel):
    """実行成果物に焼き込むマニフェスト（PROJECT.md §8.3）。"""

    run_id: str
    created_at: str  # ISO8601 (壁時計; メトリクス比較には使わない)
    orx_version: str
    git_commit: str
    config_hash: str
    root_seed: int
    world_config_name: str
    llm_mode: str
    llm_model: str
    visual_embedder: str
    duration_s: float
    condition: str = "OR-full"
