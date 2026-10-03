"""類型 5: 規範不一致（設計書 §8.2、§3.3）。

> Norm 規則が確信度 ≥ θ_5 の信念上で違反

**規範違反は「信念の上で」判定する**のが要点である。生の観測や生の記録の上で判定すると、
プローブのちらつきと記録の誤記がそのまま規程違反の起票になる。規程違反の起票は人の評価や
監査に影響するため、確信度の低い信念では起票しない——そのための θ_5（既定 0.7）である。

判定そのもの（どの規則が違反したか）は規則エンジン（M3-08、clingo）の仕事であり、この規則は
**その結果を受けて起票するかを決める**。分けているのは、規則エンジンが ASP で宣言的に書かれ、
閾値ゲートは運用で動かす値だからである。規則エンジンに閾値を埋めると、拠点ごとの上書きが
``.lp`` ファイルの書き換えになる。

根拠文書まで 3 ホップ以内で遡れること（設計書 §7.4 の監査性要件）を :class:`NormViolation` の
必須フィールドで担保する——``Norm`` が ``source_document`` と ``source_clause`` を持つ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sbp.core.logging import get_logger
from sbp.core.time import Interval
from sbp.reasoning.reconciliation.discrepancy import Discrepancy, DiscrepancyKind
from sbp.reasoning.reconciliation.severity import compute_severity

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.core.records import Norm
    from sbp.reasoning.reconciliation.context import DetectionContext

__all__ = ["NormViolation", "detect_norm"]

log = get_logger(__name__)

DETECTOR = "kind5"


@dataclass(frozen=True, slots=True)
class NormViolation:
    """規則エンジンが出した規範違反の候補（類型 5 の入力）。

    Attributes:
        norm: 違反した規範。
        subject: 違反の対象実体 IRI。
        interval: 違反が成立していた区間。
        confidence: 判定に使った信念の確信度（規則内の最小確信度を伝播した値）。
        assertions: 判定に使った信念アサーションの IRI。
        evidence: 判定に使った証拠の IRI。
    """

    norm: Norm
    subject: str
    interval: Interval
    confidence: float
    assertions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


def detect_norm(
    violations: Sequence[NormViolation],
    *,
    context: DetectionContext,
) -> list[Discrepancy]:
    """規範不一致を検出する（確信度ゲートと深刻度付け）。

    規範の発効区間外の違反候補は捨てる。失効した規程での起票は、現場から見れば誤報であり、
    照合エンジンの信用を最も早く失わせる種類の誤りである。

    Args:
        violations: 規則エンジンが出した違反候補。
        context: 検出文脈。

    Returns:
        様相 ``Open`` の不一致の列。
    """
    found: list[Discrepancy] = []
    for violation in violations:
        if violation.confidence < context.theta_norm:
            log.debug(
                "reconciliation.norm_violation_below_theta",
                norm=violation.norm.rule_ref,
                subject=violation.subject,
                confidence=violation.confidence,
                theta=context.theta_norm,
            )
            continue
        if not _norm_is_effective(violation):
            log.info(
                "reconciliation.norm_violation_outside_effective_interval",
                norm=violation.norm.rule_ref,
                subject=violation.subject,
            )
            continue
        severity = compute_severity(
            value=context.value_of(violation.subject),
            confidence=violation.confidence,
            persistence=violation.interval.duration or context.persistence,
            value_scale=context.value_scale,
            persistence_scale=context.persistence_scale,
            norm_weight=violation.norm.severity.weight,
        )
        found.append(
            Discrepancy(
                kind=DiscrepancyKind.NORM,
                subjects=(violation.subject,),
                assertions=violation.assertions,
                evidence=(
                    *violation.evidence,
                    violation.norm.source_document,
                ),
                detected_at=context.now,
                interval=violation.interval,
                severity=severity,
                detector=f"{DETECTOR}:{violation.norm.rule_ref}",
                ontology_version=context.ontology_version,
            )
        )
    return found


def _norm_is_effective(violation: NormViolation) -> bool:
    """違反区間が規範の発効区間と重なるか。"""
    effective = Interval(violation.norm.effective_from, violation.norm.effective_to)
    return effective.overlaps(violation.interval)
