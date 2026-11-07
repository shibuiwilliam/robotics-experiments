"""人の確認回答を主張イベントにする（M8-29、ADR-0018）。

設計書 §14 の手順 4: 人の回答は ``observed`` として最上位の事実性を持つ。形は取込の主張イベント
（:meth:`sbp.integration.ingest.IngestionPipeline._to_claim_event`）と同じ——
``BusinessEvent/<fluent>``、``observed``、``RecordEvidence``——にする。信念更新・照合・監査隔離は
業務記録の主張として扱うので、変更なしで人の回答を証拠にできる。回答者は ``RecordEvidence.source``
（人の IRI）に入るので、既存の ``AuditMask`` が回答者単位で
除外できる。

**確率は設定の人の確信度**（``c``）で、成立なら ``c``、不成立なら ``1 − c``。1.0 にしない——
人も誤り、信念は対数オッズで飽和するので、後から別の証拠で覆せる余地を残す。

**``event_id`` は判断から決まる**（不変条件 4）。時刻部は判断を受け付けた時刻、乱数部は
``decision_id`` から。適用が部分的に失敗して再試行されても同じ ID になり、消費側の冪等記録が弾く。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from sbp.core import vocab
from sbp.core.belief import BooleanBelief
from sbp.core.errors import IntegrationError
from sbp.core.events import (
    EventEnvelope,
    EventLayer,
    Producer,
    RecordEvidence,
    ValidTime,
    topic_name,
)
from sbp.core.ids import stable_event_id
from sbp.core.modality import Modality

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["AnswerCommand", "answer_event", "answer_record_iri", "answer_topic"]

_PRODUCER_VERSION = "0.1.0"


class AnswerCommand(BaseModel):
    """観測要求への人の確認回答（`/commands/answer_observation`。M8-29、ADR-0018）。

    **確信度を引数にしない。** 人の確信度は設定（``human_answer_confidence``）で、回答者が
    自由に決めると、同じ「見た」が回答者ごとに違う重みで信念に入る。

    Attributes:
        decision_id: 冪等の鍵。
        discrepancy_id: 観測要求の鍵の前半。**UUID に限らない**（期待の期限切れ由来は
            ``expectation:<id>``。ADR-0017）。
        fluent: 観測要求の鍵の後半（フルーエント型 IRI）。
        entity: 回答の対象実体。
        state: そのフルーエントは成立しているか。**真偽だけを受ける**（「たぶん」は回答に
            ならない。判断できないなら ``resolve_discrepancy`` の ``undetermined``）。
        note: 回答の根拠。**空を許さない**。
        answered_by: 回答した主体の IRI。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(min_length=1)
    discrepancy_id: str = Field(min_length=1)
    fluent: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    state: StrictBool
    note: str = Field(min_length=1)
    answered_by: str = Field(min_length=1)


def answer_record_iri(decision_id: str) -> str:
    """回答を指す記録の IRI（証拠の ``record``）。

    Args:
        decision_id: 判断の識別子。
    """
    return f"urn:sbp:human-answer:{decision_id}"


def answer_topic(site_id: str) -> str:
    """回答を送るトピック（業務記録の主張と同じ ``<site>.integration.business``）。

    Args:
        site_id: 拠点識別子。
    """
    return topic_name(site_id, EventLayer.INTEGRATION, "business")


def answer_event(
    *,
    site_id: str,
    decision_id: str,
    entity: str,
    fluent: str,
    state: bool,
    answered_by: str,
    at: datetime,
    confidence: float,
    uncertainty_s: float,
    ontology_version: str,
) -> EventEnvelope:
    """人の回答を ``observed`` の主張イベントにする。

    Args:
        site_id: 拠点識別子。
        decision_id: 判断の識別子（``event_id`` の鍵）。
        entity: 回答の対象実体の IRI。
        fluent: フルーエント型 IRI（``vocab`` の FluentType であること）。
        state: そのフルーエントは成立しているか。
        answered_by: 回答者の IRI（``RecordEvidence.source``。監査隔離の単位）。
        at: 回答を受け付けた時刻（有効時間の始端と ``tx_time``、``event_id`` の時刻部）。
        confidence: 人の確信度（0.5 より大きく 1 未満）。
        uncertainty_s: 有効時間の不確実幅（回答が「いつの状態」かの曖昧さ）。
        ontology_version: オントロジー版。

    Returns:
        主張イベント。

    Raises:
        IntegrationError: フルーエントが FluentType でない（信念更新が読めない）、または確信度が
            人の回答として不適切な場合。
    """
    if fluent not in vocab.FLUENT_TYPES:
        raise IntegrationError(
            f"{fluent} is not a FluentType of the ontology; the belief update could not read an "
            "answer about it (the answer would be sent and silently ignored)"
        )
    if not 0.5 < confidence < 1.0:
        raise IntegrationError(
            f"human answer confidence must be in (0.5, 1.0), got {confidence}; 1.0 would make "
            "the belief unrevisable and 0.5 or less would not be an answer"
        )
    return EventEnvelope(
        event_id=stable_event_id(at, f"human-answer|{site_id}|{decision_id}"),
        type=f"{vocab.core.BusinessEvent}/{fluent}",
        subject=entity,
        valid_time=ValidTime(start=at, uncertainty_s=uncertainty_s),
        tx_time=at,
        modality=Modality.OBSERVED,
        belief=BooleanBelief(p=confidence if state else 1.0 - confidence),
        evidence=(RecordEvidence(record=answer_record_iri(decision_id), source=answered_by),),
        producer=Producer(agent=f"human-answer@{site_id}", version=_PRODUCER_VERSION),
        ontology_version=ontology_version,
    )
