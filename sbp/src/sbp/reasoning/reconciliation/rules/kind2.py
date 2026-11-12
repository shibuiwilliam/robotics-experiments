"""類型 2: 状態不一致（設計書 §8.2）。

> 観測由来事後と記録由来尤度の乖離 JS(p_obs ‖ p_rec) ≥ θ_2 が τ 以上持続

設計書 §14 のワークドエグザンプルがこの類型である（MES は稼働と記録、物理観測は停止）。

**なぜ単純な差 |p_obs − p_rec| ではなく JS なのか。** 0.50 と 0.60 の差と 0.90 と 1.00 の差は
同じ 0.10 だが、業務上の意味はまるで違う。前者は「どちらも分からない」、後者は「一方は確信して
いるのにもう一方が否定している」。JS は後者を大きく評価する。

記録由来の確率は信頼性モデルを通した値であること（生の `payload` の真偽ではない）。誤記率
25% の情報源が「稼働」と言っても、それは確率 0.75 の主張にすぎない。この変換を飛ばすと、
雑な情報源ほど不一致を量産する。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sbp.reasoning.reconciliation.discrepancy import Discrepancy, DiscrepancyKind
from sbp.reasoning.reconciliation.series import jensen_shannon_distance
from sbp.reasoning.reconciliation.severity import compute_severity

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sbp.core.time import Interval
    from sbp.reasoning.reconciliation.context import DetectionContext
    from sbp.reasoning.reconciliation.series import BeliefSeries

__all__ = ["detect_state"]

DETECTOR = "kind2"


def detect_state(
    series: Sequence[BeliefSeries],
    *,
    evidence: Mapping[str, tuple[str, ...]] | None = None,
    context: DetectionContext,
) -> list[Discrepancy]:
    """状態不一致を検出する。

    Args:
        series: 観測由来と記録由来の両方の列を持つ信念時系列。
        evidence: 実体 IRI → 証拠 IRI の列（記録・イベント）。
        context: 検出文脈。

    Returns:
        様相 ``Open`` の不一致の列。
    """
    refs = evidence or {}
    found: list[Discrepancy] = []
    for single in series:
        for interval in single.runs_diverging(
            context.theta_state_js, persistence=context.persistence
        ):
            found.append(
                _build(
                    single, interval=interval, evidence=refs.get(single.entity, ()), context=context
                )
            )
    return found


def _build(
    single: BeliefSeries,
    *,
    interval: Interval,
    evidence: tuple[str, ...],
    context: DetectionContext,
) -> Discrepancy:
    """1 件の状態不一致を組み立てる。"""
    distances = [
        jensen_shannon_distance(observed, recorded)
        for at, observed, recorded in zip(
            single.timestamps, single.observed, single.recorded, strict=True
        )
        if recorded is not None and interval.contains_instant(at)
    ]
    # 確信度は「どれだけ食い違っているか」そのもの。乖離が大きいほど確信して起票できる
    confidence = max(distances) if distances else 0.0
    duration = interval.duration or context.persistence
    severity = compute_severity(
        value=context.value_of(single.entity),
        confidence=confidence,
        persistence=duration,
        value_scale=context.value_scale,
        persistence_scale=context.persistence_scale,
    )
    return Discrepancy(
        kind=DiscrepancyKind.STATE,
        subjects=(single.entity,),
        assertions=(single.fluent,),
        evidence=evidence,
        detected_at=context.now,
        interval=interval,
        severity=severity,
        detector=DETECTOR,
        ontology_version=context.ontology_version,
    )
