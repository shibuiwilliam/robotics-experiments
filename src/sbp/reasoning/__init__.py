"""推論・サービス層。三源統合、照合、予測、What-if、期待検証、概念発見、説明。

責務: 信念更新、照合、予測、What-if、期待検証、概念発見、スケジューリング、説明。
非責務: データ永続化（sbp.knowledge の公開 API 経由でのみ行う）。

この層が中核テーゼ 1「真実ではなく信念を管理する」と 5「不一致は例外ではなく製品」を実装する。
"""

from __future__ import annotations

from sbp.reasoning.audit import AuditMask, MaskedEvidence, no_audit
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
from sbp.reasoning.explain import (
    Explanation,
    LatentSide,
    SymbolicSide,
    explain_belief,
    explain_discrepancy,
)

__all__ = [
    "AggregationRule",
    "AuditMask",
    "BeliefFilter",
    "BeliefUpdate",
    "CouplingResult",
    "DwellTime",
    "Explanation",
    "LatentSide",
    "MaskedEvidence",
    "PartWhole",
    "SituationLink",
    "SourceContribution",
    "SymbolicSide",
    "TransitionPrior",
    "explain_belief",
    "explain_discrepancy",
    "no_audit",
    "propagate",
    "update_belief",
]
