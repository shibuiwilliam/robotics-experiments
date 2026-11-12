"""業務側の情報内容実体（ICE）— Record / PlanItem / Norm（設計書 §3.3、§7.3〜§7.4）。

中核テーゼ 2「すべての事実は誰かの主張である」に従い、業務記録も「物理実体についての主張」
として扱う。業務システムは system of record のままであり、基盤はそれを**誤りうる主張**として
受け取る（設計書 §0.2 の仮定 A3）。

- :class:`Record` — 伝票・作業指示・検査票。信頼性モデルの適用対象。
- :class:`PlanItem` — 計画項目。様相 ``planned`` のアサーションになる。
- :class:`Norm` — 規程・契約条項。様相 ``normative`` の違反判定に使う。
- :class:`UnresolvedRef` — ``is_about`` の対象を解決できなかった参照。**捨てない**。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbp.core.assertion import SCHEMA_VERSION, SemVer
from sbp.core.ids import Iri
from sbp.core.time import Interval, UtcDatetime

__all__ = [
    "BusinessEventPayload",
    "Norm",
    "NormSeverity",
    "PlanItem",
    "Record",
    "UnresolvedRef",
]


class Record(BaseModel):
    """業務システムの記録 1 件（設計書 §3.3 の ``Record``）。

    ``recorded_at`` と ``occurred_at`` を分けて持つのが要点である。両者のずれが時間不一致
    （類型 3）の材料になり、情報源信頼性モデル（§7.3）が時刻ずれ分布として学習する対象でもある。

    Attributes:
        record_id: 記録の識別子（基盤側）。
        record_type: 記録の型 IRI（``ent:WorkOrder`` など）。
        source: 情報源 IRI（``ent:CmmsSource`` など）。信頼性モデルの適用単位。
        external_id: 業務システム側の識別子（伝票番号など）。冪等な取り込みに使う。
        is_about: この記録が指す物理実体の IRI。解決できない場合は ``None`` にし、
            :class:`UnresolvedRef` を別に残す。
        recorded_at: 業務システムに記録された時刻（計上日時）。
        occurred_at: 記録が主張する「実際に起きた」時刻。記録に無ければ ``None``。
        payload: 元レコードの写像結果（業務側の属性）。写像の検証済みの形。
        reliability_profile: 適用する信頼性モデルの IRI。
        ontology_version: 取り込み時のオントロジー版。
        schema_version: この型のスキーマ版。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: UUID = Field(default_factory=uuid4)
    record_type: Iri
    source: Iri
    external_id: str
    is_about: Iri | None = None
    recorded_at: UtcDatetime
    occurred_at: UtcDatetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    reliability_profile: Iri | None = None
    ontology_version: SemVer
    schema_version: SemVer = SCHEMA_VERSION

    @property
    def is_resolved(self) -> bool:
        """``is_about`` の対象が解決済みかどうか。"""
        return self.is_about is not None

    @property
    def reporting_lag(self) -> timedelta | None:
        """記録の遅れ（``recorded_at - occurred_at``）。

        正なら事後記録、負なら先付け記録。時間不一致（類型 3）の判定と信頼性モデルの
        時刻ずれ分布の推定に使う。``occurred_at`` が無ければ ``None``。
        """
        if self.occurred_at is None:
            return None
        return self.recorded_at - self.occurred_at


class UnresolvedRef(BaseModel):
    """``is_about`` の対象を解決できなかった参照（integration ルール: 捨てない）。

    業務記録が指す物理実体が基盤側に無いのは、マスタ同期の遅れか、業務側だけが知っている
    実体か、誤記のいずれかである。どれであるかは後から判明しうるので、記録ごと捨てずに
    「未解決」として残し、再解決を試みられるようにする。

    Attributes:
        record_id: 元の記録。
        raw_reference: 業務システム側の生の参照文字列（資産 ID など）。
        reference_field: その参照が入っていたフィールド名。
        reason: 解決できなかった理由。
        attempted_at: 解決を試みた時刻。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: UUID
    raw_reference: str
    reference_field: str
    reason: Literal["not_found", "ambiguous", "malformed"]
    attempted_at: UtcDatetime


class BusinessEventPayload(BaseModel):
    """業務事象の本体（``EventEnvelope`` の中身として運ぶ）。

    設計書 §2.5 の「記録パス」で、Record を :class:`~sbp.core.events.EventEnvelope` に載せる
    ときの型付きペイロード。エンベロープ側が ``subject`` / ``valid_time`` / ``modality`` /
    ``belief`` を持つため、ここには業務側固有の情報だけを置く。

    Attributes:
        record: 元になった記録。
        event_type: 業務事象の型 IRI（``ent:GoodsReceipt`` など）。
        posting_time: 計上時刻（業務時計）。
        command_id: 基盤からの書き戻しに対する応答である場合、その ``command_id``。
            これでアクションと業務側の結果を因果 ID で結ぶ（設計書 §7.6）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record: Record
    event_type: Iri
    posting_time: UtcDatetime
    command_id: UUID | None = None


class PlanItem(BaseModel):
    """計画・スケジュールの項目（設計書 §3.3、§7.4）。

    計画は「こうする予定」という主張であり、様相 ``planned`` のアサーションになる。確信度は
    **計画種ごとの遵守率の事前分布**から与える（計画は守られないことがあるという前提を
    数値で持つ）。

    Attributes:
        plan_item_id: 計画項目の識別子。
        plan_type: 計画の型 IRI（``ent:MaintenancePlan`` など）。
        source: 情報源 IRI。
        external_id: 業務システム側の識別子。
        target: 計画の対象実体 IRI。
        planned_interval_start: 計画区間の開始。
        planned_interval_end: 計画区間の終了（右開）。
        intended_fluent: 計画が成立させようとするフルーエント型 IRI。
        adherence_prior: 当該計画種の遵守率の事前分布（0〜1）。``planned`` アサーションの
            確信度になる。
        ontology_version: 取り込み時のオントロジー版。
        schema_version: この型のスキーマ版。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_item_id: UUID = Field(default_factory=uuid4)
    plan_type: Iri
    source: Iri
    external_id: str
    target: Iri
    planned_interval_start: UtcDatetime
    planned_interval_end: UtcDatetime
    intended_fluent: Iri
    adherence_prior: float = Field(ge=0.0, le=1.0)
    ontology_version: SemVer
    schema_version: SemVer = SCHEMA_VERSION

    @model_validator(mode="after")
    def _interval_is_right_open(self) -> Self:
        """計画区間が右開の非空区間であることを検証する（不変条件 8）。"""
        Interval(self.planned_interval_start, self.planned_interval_end)
        return self

    @property
    def planned_interval(self) -> Interval:
        """計画区間。"""
        return Interval(self.planned_interval_start, self.planned_interval_end)


class NormSeverity(StrEnum):
    """規範違反の重大度。深刻度モデル（設計書 §8.2）の重み付けに使う。"""

    ADVISORY = "advisory"
    """助言。違反しても業務は進む。"""
    IMPORTANT = "important"
    """重要。SLA やコストに影響する。"""
    CRITICAL = "critical"
    """重大。安全・法令に関わる。人の承認を必須にする。"""

    @property
    def weight(self) -> float:
        """深刻度計算に用いる重み。"""
        return {"advisory": 0.2, "important": 0.6, "critical": 1.0}[self.value]


class Norm(BaseModel):
    """規程・契約条項・安全規則（設計書 §3.3、§7.4）。

    規範は Datalog / ASP 規則にコンパイルされ、違反判定の結果が様相 ``normative`` の
    アサーションになる。**出典（文書と節）を必ず持つ**のが要点である。規範違反は人の判断に
    影響するため、根拠文書まで 3 ホップ以内で遡れなければ監査に使えない（非機能要求の監査性）。

    Attributes:
        norm_id: 規範の識別子。
        norm_type: 規範の型 IRI。
        rule_ref: 規則の参照（``ontology/norms/*.lp`` の規則名）。
        source_document: 出典文書の IRI。
        source_clause: 出典の節（文書内位置）。
        severity: 違反の重大度。
        applies_to_type: 適用対象の実体型 IRI。
        effective_from: 規範の発効時刻。
        effective_to: 失効時刻（無期限なら ``None``）。
        ontology_version: 取り込み時のオントロジー版。
        schema_version: この型のスキーマ版。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    norm_id: UUID = Field(default_factory=uuid4)
    norm_type: Iri
    rule_ref: str
    source_document: Iri
    source_clause: str
    severity: NormSeverity
    applies_to_type: Iri
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None = None
    ontology_version: SemVer
    schema_version: SemVer = SCHEMA_VERSION

    @model_validator(mode="after")
    def _effective_interval_is_right_open(self) -> Self:
        """発効区間が右開の非空区間であることを検証する。"""
        Interval(self.effective_from, self.effective_to)
        return self

    def is_effective_at(self, instant: datetime) -> bool:
        """指定時刻に発効しているかどうか。

        Args:
            instant: 判定する時刻。
        """
        return Interval(self.effective_from, self.effective_to).contains_instant(instant)
