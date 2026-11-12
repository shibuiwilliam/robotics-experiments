"""イベント — 記録源（設計書 §6.1、§5.8）。

不変条件 4 により、全イベントは UUIDv7 の ``event_id`` と ``schema_version`` を持ち、
``event_id`` で冪等。順序保証は実体単位（パーティションキー＝主体実体 IRI）であり、
グローバル順序を仮定しない。

イベントは「状態の投影元」であって状態そのものではない。状態テーブルを直接更新して整合させる
ことは禁止（CLAUDE.md）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbp.core.assertion import SCHEMA_VERSION, SemVer
from sbp.core.belief import Belief
from sbp.core.ids import EventId, Iri, new_event_id
from sbp.core.modality import Modality
from sbp.core.time import Interval, UtcDatetime

__all__ = [
    "EventEnvelope",
    "EventLayer",
    "Evidence",
    "ProbeEvidence",
    "Producer",
    "RecordEvidence",
    "RuleEvidence",
    "ValidTime",
    "topic_name",
]


class EventLayer(StrEnum):
    """イベントを生成した層。トピック名 ``<site>.<layer>.<class>`` の第 2 要素。"""

    BRIDGE = "bridge"
    """接地層（プローブ由来の物理事象）。"""
    INTEGRATION = "integration"
    """業務統合層（業務記録由来の事象）。"""
    REASONING = "reasoning"
    """推論層（不一致・期待・予測）。"""
    ACTIONS = "actions"
    """アクション層（実行・承認・補償）。"""
    WORLDMODEL = "worldmodel"
    """WM 層（ドリフト通知・版昇格）。"""


def topic_name(site_id: str, layer: EventLayer, event_class: str) -> str:
    """トピック名 ``<site>.<layer>.<class>`` を組み立てる（設計書 §6.1）。

    トピックの追加は ``scripts/topics.py`` に書き ``make seed`` で作成する（手作業禁止）。

    Args:
        site_id: 拠点識別子（例 ``siteA``、中央は ``central``）。
        layer: 生成層。
        event_class: イベントの種別（例 ``physical``, ``business``, ``discrepancy``）。
    """
    for name, part in (("site_id", site_id), ("event_class", event_class)):
        if not part or "." in part or any(c.isspace() for c in part):
            raise ValueError(f"invalid {name} for topic name: {part!r}")
    return f"{site_id}.{layer.value}.{event_class}"


class ValidTime(BaseModel):
    """事象の有効時間。右開区間 ＋ 不確実幅（設計書 §5.8）。

    事象昇格は変化点の不確実性を必ず持つため、``uncertainty_s`` を必須にして点時刻の
    イベントを出させない（``.claude/rules/bridge-worldmodel.md``）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: UtcDatetime
    end: UtcDatetime | None = None
    uncertainty_s: float = Field(ge=0.0)
    """変化点推定の不確実幅（秒）。"""

    @model_validator(mode="after")
    def _right_open(self) -> Self:
        Interval(self.start, self.end)
        return self

    @property
    def interval(self) -> Interval:
        """区間表現。"""
        return Interval(self.start, self.end)


class _EvidenceBase(BaseModel):
    """証拠の共通部分。生スコアはここに残す（不変条件 7）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ProbeEvidence(_EvidenceBase):
    """プローブ由来の証拠。

    Attributes:
        probe_id: プローブの IRI。
        wm_version: 用いた WM 版の IRI。
        window: 観測窓。
        raw_score: 較正前の生スコア（``belief`` には入れない）。
    """

    kind: Literal["probe"] = "probe"
    probe_id: Iri
    wm_version: Iri | None = None
    window: ValidTime | None = None
    raw_score: float | None = None


class RecordEvidence(_EvidenceBase):
    """業務記録由来の証拠。

    Attributes:
        record: 記録（ICE）の IRI。
        source: DataSource の IRI。
        source_reliability: 適用した信頼性モデルの IRI。
    """

    kind: Literal["record"] = "record"
    record: Iri
    source: Iri
    source_reliability: Iri | None = None


class RuleEvidence(_EvidenceBase):
    """規則由来の証拠。

    Attributes:
        rule: 規則の IRI（``ontology/process/*.lp`` または ``norms/*.lp`` の規則）。
        norm: 規範の出典 IRI（規範判定の場合）。
    """

    kind: Literal["rule"] = "rule"
    rule: Iri
    norm: Iri | None = None


Evidence = Annotated[
    ProbeEvidence | RecordEvidence | RuleEvidence,
    Field(discriminator="kind"),
]
"""証拠の判別可能 union。"""


class Producer(BaseModel):
    """イベントの生成主体。

    Attributes:
        agent: 生成エージェント（例 ``bridge@siteA``）。
        version: 生成コードの版。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str
    version: str


class EventEnvelope(BaseModel):
    """全イベント共通のエンベロープ（設計書 §6.1）。

    パーティションキーは :attr:`subject`（主体実体 IRI）。これにより実体単位の順序が保証される。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: EventId = Field(default_factory=new_event_id)
    """UUIDv7。重複排除の鍵（不変条件 4）。"""
    schema_version: SemVer = SCHEMA_VERSION
    type: Iri
    """イベント型の IRI（``sbp-core:PhysicalEvent/ent:PumpStopped`` のような複合型も許す）。"""
    subject: Iri
    """主体実体の IRI。パーティションキーであり順序保証の単位。"""

    valid_time: ValidTime
    tx_time: UtcDatetime
    """基盤がこのイベントを記録した時刻。"""

    modality: Modality
    belief: Belief
    evidence: tuple[Evidence, ...] = ()

    producer: Producer
    causation_id: UUID | None = None
    """このイベントを引き起こした起点イベントの ``event_id``。"""
    correlation_id: UUID | None = None
    """同一 Situation に属するイベント群を束ねる識別子。"""

    situation: Iri | None = None
    ontology_version: SemVer

    @model_validator(mode="after")
    def _inference_outputs_are_inferred(self) -> Self:
        """プローブ・規則由来の証拠を持つイベントが ``observed`` を名乗らないことを検証する。

        不変条件 3: プローブ・規則・LLM・エージェントの出力は必ず ``inferred``。
        """
        if self.modality is Modality.OBSERVED:
            derived = [e.kind for e in self.evidence if e.kind in ("probe", "rule")]
            if derived:
                raise ValueError(
                    f"modality=observed cannot carry derived evidence {derived}; "
                    "probe/rule outputs must be inferred (invariant 3)"
                )
        return self

    @property
    def partition_key(self) -> bytes:
        """Kafka のパーティションキー（主体実体 IRI のバイト列）。"""
        return self.subject.encode("utf-8")
