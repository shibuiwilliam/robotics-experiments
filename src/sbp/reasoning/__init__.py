"""推論・サービス層。三源統合、照合、予測、What-if、期待検証、概念発見、説明。

責務: 信念更新、照合、予測、What-if、期待検証、概念発見、スケジューリング、説明。
非責務: データ永続化（sbp.knowledge の公開 API 経由でのみ行う）。

この層が中核テーゼ 1「真実ではなく信念を管理する」と 5「不一致は例外ではなく製品」を実装する。
"""

from __future__ import annotations

from sbp.core.audit import AuditMask, MaskedEvidence, no_audit
from sbp.reasoning.belief import (
    BeliefFilter,
    BeliefUpdate,
    DwellTime,
    SourceContribution,
    TransitionPrior,
    update_belief,
)
from sbp.reasoning.coupling import (
    AggregationRule,
    CouplingResult,
    PartWhole,
    SituationLink,
    propagate,
)
from sbp.reasoning.expectation import (
    Expectation,
    ExpectationStatus,
    ExpectedEffect,
    VerificationInputs,
    VerificationMethod,
    VerificationResult,
    verify_expectation,
)
from sbp.reasoning.explain import (
    Explanation,
    LatentSide,
    SymbolicSide,
    explain_belief,
    explain_discrepancy,
)
from sbp.reasoning.prediction import (
    AccuracyReport,
    Forecast,
    ForecastPoint,
    reliable_lead_time,
    score_forecasts,
)
from sbp.reasoning.rules import (
    Derivation,
    Fact,
    NormRule,
    Propagation,
    RuleProgram,
    RuleResult,
    propagate_confidence,
)
from sbp.reasoning.whatif import (
    InterventionEncoding,
    Scenario,
    ScenarioKind,
    ValidityScore,
    encode_intervention,
    promote_scenario,
    run_scenario,
)

__all__ = [
    "AccuracyReport",
    "AggregationRule",
    "AuditMask",
    "BeliefFilter",
    "BeliefUpdate",
    "CouplingResult",
    "Derivation",
    "DwellTime",
    "Expectation",
    "ExpectationStatus",
    "ExpectedEffect",
    "Explanation",
    "Fact",
    "Forecast",
    "ForecastPoint",
    "InterventionEncoding",
    "LatentSide",
    "MaskedEvidence",
    "NormRule",
    "PartWhole",
    "Propagation",
    "RuleProgram",
    "RuleResult",
    "Scenario",
    "ScenarioKind",
    "SituationLink",
    "SourceContribution",
    "SymbolicSide",
    "TransitionPrior",
    "ValidityScore",
    "VerificationInputs",
    "VerificationMethod",
    "VerificationResult",
    "encode_intervention",
    "explain_belief",
    "explain_discrepancy",
    "no_audit",
    "promote_scenario",
    "propagate",
    "propagate_confidence",
    "reliable_lead_time",
    "run_scenario",
    "score_forecasts",
    "update_belief",
    "verify_expectation",
]
