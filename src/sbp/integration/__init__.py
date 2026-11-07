"""業務統合層。コネクタ、写像、BusinessEvent 化、信頼性モデル、計画・規範取込、書き戻し。

責務: コネクタ、写像、業務事象化、情報源信頼性モデル、書き戻し。
非責務: 業務システムの代替（ERP/MES は system of record のまま）。

この層の出力はイベントであり、信念への反映は ``reasoning`` が行う（層の依存の向き）。
"""

from __future__ import annotations

from sbp.integration.connectors import Connector, RawRecord, SchemaFingerprint
from sbp.integration.extraction import (
    EvidenceSpan,
    ExtractedField,
    ExtractionResult,
    NormExtraction,
    verify_spans,
)
from sbp.integration.human_answer import answer_event, answer_record_iri, answer_topic
from sbp.integration.ingest import IngestionPipeline, IngestResult, MappingDriftDetected
from sbp.integration.mappings import Claim, Mapping, MappingRegistry, MappingResult
from sbp.integration.master import MasterDataSync, MasterSyncReport
from sbp.integration.plans import (
    NormRule,
    PlanSync,
    intended_fluent_for,
    plan_item_from_record,
    plan_provenance,
    to_planned_assertion,
)
from sbp.integration.reliability import (
    ConfusionMatrix,
    ReliabilityModel,
    ReliabilityRegistry,
    SystematicBias,
    TimeOffsetProfile,
)
from sbp.integration.resolver import (
    EntityResolver,
    MasterDataResolver,
    ResolutionOutcome,
    resolve_subject,
)
from sbp.integration.writeback import (
    WritebackCommand,
    WritebackExecutor,
    WritebackOutcome,
    WritebackResult,
    WritebackTarget,
)

__all__ = [
    "Claim",
    "ConfusionMatrix",
    "Connector",
    "EntityResolver",
    "EvidenceSpan",
    "ExtractedField",
    "ExtractionResult",
    "IngestResult",
    "IngestionPipeline",
    "Mapping",
    "MappingDriftDetected",
    "MappingRegistry",
    "MappingResult",
    "MasterDataResolver",
    "MasterDataSync",
    "MasterSyncReport",
    "NormExtraction",
    "NormRule",
    "PlanSync",
    "RawRecord",
    "ReliabilityModel",
    "ReliabilityRegistry",
    "ResolutionOutcome",
    "SchemaFingerprint",
    "SystematicBias",
    "TimeOffsetProfile",
    "WritebackCommand",
    "WritebackExecutor",
    "WritebackOutcome",
    "WritebackResult",
    "WritebackTarget",
    "answer_event",
    "answer_record_iri",
    "answer_topic",
    "intended_fluent_for",
    "plan_item_from_record",
    "plan_provenance",
    "resolve_subject",
    "to_planned_assertion",
    "verify_spans",
]
