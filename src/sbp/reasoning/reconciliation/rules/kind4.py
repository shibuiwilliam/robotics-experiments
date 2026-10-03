"""類型 4: 同一性不一致（設計書 §8.2、§5.4）。

> IdentityClaim 撤回、競合クレーム（同一 Track に複数実体、同一実体に複数 Track）

同一性の食い違いは**他のすべての不一致を汚染する**ため独立の類型になっている。トラックと業務
実体の束縛が間違っていれば、そのトラックの観測で更新した信念は別の実体の信念であり、そこから
出る類型 1・2・3 の不一致はすべて見当違いになる。だから同一性不一致は深刻度を独立に高く持ち、
解決を他より先に要求する。

**Person のトラックは対象にしない。** 設計書 §5.4 の通り、人のトラックは業務 ID に束縛しない
——束縛しないものに同一性不一致は起きない。入力に Person トラックの主張が混ざっていたら
それは束縛側のバグであり、ここで検出するのではなく ``bridge.identity`` が拒否する。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sbp.core.time import Interval, ensure_utc
from sbp.reasoning.reconciliation.discrepancy import Discrepancy, DiscrepancyKind
from sbp.reasoning.reconciliation.severity import compute_severity

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sbp.bridge.identity import IdentityClaim
    from sbp.reasoning.reconciliation.context import DetectionContext

__all__ = ["detect_identity"]

DETECTOR = "kind4"


def detect_identity(
    claims: Sequence[IdentityClaim],
    *,
    context: DetectionContext,
) -> list[Discrepancy]:
    """同一性不一致を検出する。

    撤回された主張と、競合する主張（1 トラック対多実体、1 実体対多トラック）を起票する。

    Args:
        claims: 判定対象の同一性主張。
        context: 検出文脈。

    Returns:
        様相 ``Open`` の不一致の列。
    """
    found = [_build_revocation(claim, context=context) for claim in claims if not claim.is_active]
    found.extend(_detect_conflicts(claims, context=context))
    return found


def _build_revocation(claim: IdentityClaim, *, context: DetectionContext) -> Discrepancy:
    """撤回された束縛 1 件を起票する。"""
    start = ensure_utc(claim.valid_from)
    end = ensure_utc(claim.valid_to) if claim.valid_to is not None else None
    interval = Interval(start, end if end is not None and end > start else None)
    severity = compute_severity(
        value=context.value_of(claim.entity),
        # 撤回時の確信度は「束縛が成り立たない確信」なので主張の確信度の裏返し
        confidence=1.0 - claim.confidence,
        persistence=interval.duration or context.persistence,
        value_scale=context.value_scale,
        persistence_scale=context.persistence_scale,
    )
    return Discrepancy(
        kind=DiscrepancyKind.IDENTITY,
        subjects=(claim.entity,),
        evidence=(str(claim.claim_id),),
        detected_at=context.now,
        interval=interval,
        severity=severity,
        detector=f"{DETECTOR}:revoked",
        ontology_version=context.ontology_version,
    )


def _detect_conflicts(
    claims: Sequence[IdentityClaim], *, context: DetectionContext
) -> list[Discrepancy]:
    """有効な主張のあいだの競合を探す。"""
    active = [claim for claim in claims if claim.is_active]
    by_track: dict[UUID, list[IdentityClaim]] = {}
    by_entity: dict[str, list[IdentityClaim]] = {}
    for claim in active:
        by_track.setdefault(claim.track_id, []).append(claim)
        by_entity.setdefault(claim.entity, []).append(claim)

    found: list[Discrepancy] = []
    for group in by_track.values():
        entities = {claim.entity for claim in group}
        if len(entities) > 1:
            found.append(
                _build_conflict(
                    group,
                    subjects=tuple(sorted(entities)),
                    direction="track_bound_to_many_entities",
                    context=context,
                )
            )
    for entity, group in by_entity.items():
        tracks = {claim.track_id for claim in group}
        if len(tracks) > 1:
            found.append(
                _build_conflict(
                    group,
                    subjects=(entity,),
                    direction="entity_bound_to_many_tracks",
                    context=context,
                )
            )
    return found


def _build_conflict(
    group: Sequence[IdentityClaim],
    *,
    subjects: tuple[str, ...],
    direction: str,
    context: DetectionContext,
) -> Discrepancy:
    """競合する主張の組を 1 件の不一致にする。"""
    start = min(ensure_utc(claim.valid_from) for claim in group)
    ends = [claim.valid_to for claim in group]
    end = None if any(value is None for value in ends) else max(ensure_utc(v) for v in ends if v)
    interval = Interval(start, end if end is not None and end > start else None)
    # 競合は「どちらも確信している」ほど深刻なので最小の確信度を採る
    confidence = min(claim.confidence for claim in group)
    severity = compute_severity(
        value=context.value_of(subjects[0]),
        confidence=confidence,
        persistence=interval.duration or context.persistence,
        value_scale=context.value_scale,
        persistence_scale=context.persistence_scale,
    )
    return Discrepancy(
        kind=DiscrepancyKind.IDENTITY,
        subjects=subjects,
        evidence=tuple(str(claim.claim_id) for claim in group),
        detected_at=context.now,
        interval=interval,
        severity=severity,
        detector=f"{DETECTOR}:{direction}",
        ontology_version=context.ontology_version,
    )
