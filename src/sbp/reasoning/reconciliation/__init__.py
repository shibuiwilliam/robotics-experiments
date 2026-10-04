"""照合（不一致の検出・統合・抑制・ライフサイクル）。設計書 §8.2。

中核テーゼ 5「不一致は例外ではなく製品」の実装層。入口は :func:`reconcile`（検出候補 →
統合 → 抑制 → トリアージ）と五類型の検出規則（:mod:`sbp.reasoning.reconciliation.rules`）。
"""

from __future__ import annotations

from sbp.reasoning.reconciliation.context import DetectionContext, SiteOverride
from sbp.reasoning.reconciliation.discrepancy import (
    Discrepancy,
    DiscrepancyKind,
    DiscrepancyStatus,
    LearningSignals,
    Resolution,
    ResolutionOutcome,
    StatusChange,
    Suppression,
)
from sbp.reasoning.reconciliation.engine import (
    ReconciliationOutcome,
    explain_as_systematic,
    merge_duplicates,
    reconcile,
)
from sbp.reasoning.reconciliation.learning import (
    ConceptCandidate,
    LearningArtifacts,
    ObservationRequest,
    apply_resolution,
    convert_resolution,
)
from sbp.reasoning.reconciliation.notify import (
    NOTIFY_VERSION,
    discrepancy_event,
    discrepancy_type_iri,
)
from sbp.reasoning.reconciliation.persistence import from_record, to_record
from sbp.reasoning.reconciliation.rules import (
    NormViolation,
    RecordedPresence,
    TimedClaim,
    detect_existence,
    detect_identity,
    detect_norm,
    detect_state,
    detect_time,
)
from sbp.reasoning.reconciliation.series import (
    BeliefSeries,
    jensen_shannon_distance,
    sustained_runs,
)
from sbp.reasoning.reconciliation.severity import (
    BusinessValue,
    BusinessValueRegistry,
    Severity,
    compute_severity,
)

__all__ = [
    "NOTIFY_VERSION",
    "BeliefSeries",
    "BusinessValue",
    "BusinessValueRegistry",
    "ConceptCandidate",
    "DetectionContext",
    "Discrepancy",
    "DiscrepancyKind",
    "DiscrepancyStatus",
    "LearningArtifacts",
    "LearningSignals",
    "NormViolation",
    "ObservationRequest",
    "ReconciliationOutcome",
    "RecordedPresence",
    "Resolution",
    "ResolutionOutcome",
    "Severity",
    "SiteOverride",
    "StatusChange",
    "Suppression",
    "TimedClaim",
    "apply_resolution",
    "compute_severity",
    "convert_resolution",
    "detect_existence",
    "detect_identity",
    "detect_norm",
    "detect_state",
    "detect_time",
    "discrepancy_event",
    "discrepancy_type_iri",
    "explain_as_systematic",
    "from_record",
    "jensen_shannon_distance",
    "merge_duplicates",
    "reconcile",
    "sustained_runs",
    "to_record",
]
