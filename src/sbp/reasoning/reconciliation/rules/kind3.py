"""類型 3: 時間不一致（設計書 §8.2、§3.6）。

> |t_observed − t_recorded| > 許容プロファイルの 95 パーセンタイル、または順序制約違反

**閾値をコードに書かず情報源ごとの許容プロファイルから導く**のが要点である。MES の自動連携で
5 分遅れたら異常だが、手書き日報が 5 時間遅れるのは正常である。固定閾値で両方を見ると、
前者を見逃して後者を洪水のように起票する。

順序制約違反は量ではなく**順序**の食い違いである。「物理では点検の後に稼働再開、記録では
稼働再開の後に点検」のように、どれだけずれたかではなく前後が入れ替わっていることが問題。
遅れの大小に吸収されないよう独立に判定する。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING

from sbp.core.time import Interval, ensure_utc
from sbp.reasoning.reconciliation.discrepancy import Discrepancy, DiscrepancyKind
from sbp.reasoning.reconciliation.severity import compute_severity

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

    from sbp.core.records import Record
    from sbp.reasoning.reconciliation.context import DetectionContext

__all__ = ["TimedClaim", "detect_time"]

DETECTOR = "kind3"


@dataclass(frozen=True, slots=True)
class TimedClaim:
    """物理観測と業務記録が同じ事象について主張する時刻の対（類型 3 の入力）。

    Attributes:
        subject: 対象実体の IRI。
        event_type: 事象型の IRI（``ent:StopCmd`` など）。
        observed_at: 物理観測（事象昇格）が主張する発生時刻。
        record: 対応する業務記録。
        event_iri: 昇格したイベントの IRI（証拠）。
    """

    subject: str
    event_type: str
    observed_at: datetime
    record: Record
    event_iri: str = ""

    @property
    def recorded_at(self) -> datetime:
        """記録が主張する発生時刻（``occurred_at`` が無ければ計上時刻）。"""
        return self.record.occurred_at or self.record.recorded_at

    @property
    def offset(self) -> timedelta:
        """記録時刻 − 観測時刻（正なら記録が遅い）。"""
        return ensure_utc(self.recorded_at) - ensure_utc(self.observed_at)


def detect_time(
    claims: Sequence[TimedClaim],
    *,
    context: DetectionContext,
) -> list[Discrepancy]:
    """時間不一致（ずれと順序違反）を検出する。

    Args:
        claims: 観測時刻と記録時刻の対。
        context: 検出文脈。

    Returns:
        様相 ``Open`` の不一致の列。
    """
    found = [
        _build_offset(claim, context=context)
        for claim in claims
        if context.reliability.for_record(claim.record).time_offset.is_implausible(
            claim.offset, percentile=context.time_tolerance_percentile
        )
    ]
    found.extend(_detect_order_violations(claims, context=context))
    return found


def _detect_order_violations(
    claims: Sequence[TimedClaim], *, context: DetectionContext
) -> list[Discrepancy]:
    """観測と記録で前後が入れ替わっている対を探す。"""
    by_subject: dict[str, list[TimedClaim]] = {}
    for claim in claims:
        by_subject.setdefault(claim.subject, []).append(claim)

    found: list[Discrepancy] = []
    for subject, group in by_subject.items():
        for first, second in combinations(group, 2):
            first_observed = ensure_utc(first.observed_at)
            second_observed = ensure_utc(second.observed_at)
            first_recorded = ensure_utc(first.recorded_at)
            second_recorded = ensure_utc(second.recorded_at)
            # 同時刻の対には矛盾しうる「順序」が無い。等号を「より前」と扱うと、
            # 片側だけが同時の対がすべて順序違反になる（順序を発明してしまう）
            if first_observed == second_observed or first_recorded == second_recorded:
                continue
            if (first_observed < second_observed) == (first_recorded < second_recorded):
                continue
            start = min(ensure_utc(first.observed_at), ensure_utc(second.observed_at))
            end = max(ensure_utc(first.recorded_at), ensure_utc(second.recorded_at))
            interval = Interval(start, end if end > start else None)
            severity = compute_severity(
                value=context.value_of(subject),
                # 順序違反は量に依らず明確な矛盾なので確信度は高く固定する
                confidence=1.0,
                persistence=interval.duration or context.persistence,
                value_scale=context.value_scale,
                persistence_scale=context.persistence_scale,
            )
            found.append(
                Discrepancy(
                    kind=DiscrepancyKind.TIME,
                    subjects=(subject,),
                    assertions=(first.event_type, second.event_type),
                    evidence=tuple(ref for ref in (first.event_iri, second.event_iri) if ref),
                    detected_at=context.now,
                    interval=interval,
                    severity=severity,
                    detector=f"{DETECTOR}:order",
                    ontology_version=context.ontology_version,
                )
            )
    return found


def _build_offset(claim: TimedClaim, *, context: DetectionContext) -> Discrepancy:
    """ずれ超過 1 件を組み立てる。"""
    profile = context.reliability.for_record(claim.record).time_offset
    tolerance = profile.percentile_s(context.time_tolerance_percentile)
    offset_s = abs(claim.offset.total_seconds())
    # 許容の何倍ずれたかを確信度に写す（2 倍で 1.0 に飽和）
    confidence = min(1.0, max(0.0, (offset_s - tolerance) / max(tolerance, 1.0)))
    observed = ensure_utc(claim.observed_at)
    recorded = ensure_utc(claim.recorded_at)
    start, end = min(observed, recorded), max(observed, recorded)
    interval = Interval(start, end if end > start else None)
    severity = compute_severity(
        value=context.value_of(claim.subject),
        confidence=confidence,
        persistence=interval.duration or context.persistence,
        value_scale=context.value_scale,
        persistence_scale=context.persistence_scale,
    )
    return Discrepancy(
        kind=DiscrepancyKind.TIME,
        subjects=(claim.subject,),
        assertions=(claim.event_type,),
        evidence=tuple(ref for ref in (claim.event_iri,) if ref),
        detected_at=context.now,
        interval=interval,
        severity=severity,
        detector=f"{DETECTOR}:offset",
        ontology_version=context.ontology_version,
    )
