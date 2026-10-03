"""類型 1: 存在不一致（設計書 §8.2）。

> (a) P(present(x, L)) ≥ θ_1 が τ 以上持続 ∧ 記録に x@L なし
> (b) 記録 x@L ∧ P(present) ≤ 1−θ_1 が τ 以上持続

**(a) と (b) は別の業務問題である。** (a) は「物理にあるのに帳簿に無い」——未計上の在庫、
無届の設備移動。(b) は「帳簿にあるのに物理に無い」——幽霊在庫、持ち出されたまま記録が
残っている資産。同じ類型だが解決の担当も処置も違うため、``direction`` を証拠として残す。

「記録が無い」と「記録が『無い』と言っている」を混同しないのが実装上の要点である。前者は
沈黙（記録漏れかもしれないし、記録する習慣が無いだけかもしれない）で、後者は主張である。
沈黙に対して (a) を起票するのは、**その場所について記録があるはずだと分かっているとき**だけ
でよい——そのため ``expect_records_for`` で明示的に指定させる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sbp.reasoning.reconciliation.discrepancy import Discrepancy, DiscrepancyKind
from sbp.reasoning.reconciliation.severity import compute_severity

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sbp.core.time import Interval
    from sbp.reasoning.reconciliation.context import DetectionContext
    from sbp.reasoning.reconciliation.series import BeliefSeries

__all__ = ["RecordedPresence", "detect_existence"]

DETECTOR = "kind1"


@dataclass(frozen=True, slots=True)
class RecordedPresence:
    """記録が主張する存在（類型 1 の入力）。

    Attributes:
        record_iri: 記録の IRI（証拠として残す）。
        present: 記録が「ある」と言っているか。
        source: 情報源 IRI。
        record_type: 記録型 IRI。
    """

    record_iri: str
    present: bool
    source: str
    record_type: str


def detect_existence(
    series: Sequence[BeliefSeries],
    *,
    recorded: Mapping[str, RecordedPresence],
    expect_records_for: frozenset[str] = frozenset(),
    context: DetectionContext,
) -> list[Discrepancy]:
    """存在不一致を検出する。

    Args:
        series: 存在フルーエント（``ent:Present`` など）の信念時系列。
        recorded: 実体 IRI → 記録が主張する存在。入っていない実体は「記録が沈黙している」。
        expect_records_for: 記録があるはずだと分かっている実体の集合。沈黙に対して (a) を
            起票するのはこの集合に属する実体のみ。
        context: 検出文脈。

    Returns:
        様相 ``Open`` の不一致の列。
    """
    theta = context.theta_existence
    found: list[Discrepancy] = []

    for single in series:
        claim = recorded.get(single.entity)

        # (a) 物理にあるのに記録が無い／記録が「無い」と言っている
        silent_but_expected = claim is None and single.entity in expect_records_for
        if silent_but_expected or (claim is not None and not claim.present):
            for interval in single.runs_above(theta, persistence=context.persistence):
                found.append(
                    _build(
                        single,
                        interval=interval,
                        direction="physical_present_unrecorded",
                        claim=claim,
                        context=context,
                    )
                )

        # (b) 記録にあるのに物理に無い（幽霊在庫）
        if claim is not None and claim.present:
            for interval in single.runs_below(1.0 - theta, persistence=context.persistence):
                found.append(
                    _build(
                        single,
                        interval=interval,
                        direction="recorded_present_absent",
                        claim=claim,
                        context=context,
                    )
                )
    return found


def _build(
    single: BeliefSeries,
    *,
    interval: Interval,
    direction: str,
    claim: RecordedPresence | None,
    context: DetectionContext,
) -> Discrepancy:
    """1 件の存在不一致を組み立てる。"""
    mean = single.mean_observed(interval)
    # (b) は「無い」ことの確信度なので反転して確信度にする
    confidence = mean if direction == "physical_present_unrecorded" else 1.0 - mean
    duration = interval.duration or context.persistence
    severity = compute_severity(
        value=context.value_of(single.entity),
        confidence=confidence,
        persistence=duration,
        value_scale=context.value_scale,
        persistence_scale=context.persistence_scale,
    )
    return Discrepancy(
        kind=DiscrepancyKind.EXISTENCE,
        subjects=(single.entity,),
        assertions=(single.fluent,),
        evidence=(claim.record_iri,) if claim is not None else (),
        detected_at=context.now,
        interval=interval,
        severity=severity,
        detector=f"{DETECTOR}:{direction}",
        ontology_version=context.ontology_version,
    )
