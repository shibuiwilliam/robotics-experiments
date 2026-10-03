"""core — 全層が依存する型の根。

公開するのは契約（pydantic モデル）と時間・ID・様相・設定・例外のみ。ここに I/O や
ドメインロジックを置かない（上位層は core の型と eventlog のイベントだけで結合する）。
"""

from __future__ import annotations

from sbp.core.assertion import SCHEMA_VERSION, Assertion, SemVer
from sbp.core.belief import (
    Belief,
    BooleanBelief,
    CategoricalBelief,
    NumericBelief,
    Uncertainty,
)
from sbp.core.events import (
    EventEnvelope,
    EventLayer,
    Evidence,
    ProbeEvidence,
    Producer,
    RecordEvidence,
    RuleEvidence,
    ValidTime,
    topic_name,
)
from sbp.core.ids import EventId, Iri, new_event_id, site_iri
from sbp.core.modality import INFERENCE_OUTPUT_MODALITY, MAIN_BELIEF_MODALITIES, Modality
from sbp.core.time import AllenRelation, Interval, UtcDatetime, allen_relation, ensure_utc, now

__all__ = [
    "INFERENCE_OUTPUT_MODALITY",
    "MAIN_BELIEF_MODALITIES",
    "SCHEMA_VERSION",
    "AllenRelation",
    "Assertion",
    "Belief",
    "BooleanBelief",
    "CategoricalBelief",
    "EventEnvelope",
    "EventId",
    "EventLayer",
    "Evidence",
    "Interval",
    "Iri",
    "Modality",
    "NumericBelief",
    "ProbeEvidence",
    "Producer",
    "RecordEvidence",
    "RuleEvidence",
    "SemVer",
    "Uncertainty",
    "UtcDatetime",
    "ValidTime",
    "allen_relation",
    "ensure_utc",
    "new_event_id",
    "now",
    "site_iri",
    "topic_name",
]
