"""証拠の三源並置の組み立て（M8-17、ADR-0020）。

信念の時系列（``inferred``）と、各点の三源の寄与（``belief_contributions``）と、計画（``planned``）を
**実体 × フルーエント × 区間**で突き合わせる。純粋な関数で、API の型への変換は
:mod:`sbp.api.schema`。

**並置の単位は寄与である。** 三源は様相では分かれない（業務記録由来の信念も推定を経て ``inferred``。
所見 84）。各点が「観測・記録・計画のどれがどれだけ事後を動かしたか」を持ち、その時系列を並べる。

**寄与が無い点を、寄与 0 と区別する。** 寄与の行が無いのは「記録されていない」
（この変更より前の信念、または結合が書いた信念）であって、「どの源も動かさなかった」ではない。
前者を 0 に丸めると、画面は
「観測も記録も計画も効いていない」と誤読させる（所見 32）。

**行を突き合わせる側はアサーション。** 寄与だけ残った孤児（アサーションの書き込みに失敗した）や、
ABAC で見えない文の寄与は、点として出さない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from uuid import UUID

    from sbp.knowledge.assertions import AssertionRow
    from sbp.knowledge.contributions import ContributionRecord

__all__ = ["EvidencePoint", "PlannedSpan", "build_evidence"]

Origin = Literal["update", "coupling"]
"""信念の出どころ。``update`` は前向きフィルタ、``coupling`` は実体間の結合（寄与を持たない）。"""

_COUPLING_MARK = "/coupling/"
"""結合が書く信念の来歴バンドルに入る目印（belief-worker の ``_couple``）。"""


@dataclass(frozen=True, slots=True)
class EvidencePoint:
    """信念の 1 点と、その三源の寄与。

    Attributes:
        assertion_id: アサーションの識別子。
        at: 信念の有効開始。
        probability: 較正済みの確率。
        origin: 出どころ（前向きフィルタ / 結合）。
        contribution: 三源の寄与。``None`` は**記録されていない**（0 ではない）。
    """

    assertion_id: UUID
    at: datetime
    probability: float
    origin: Origin
    contribution: ContributionRecord | None


@dataclass(frozen=True, slots=True)
class PlannedSpan:
    """区間と重なる計画。

    Attributes:
        start: 計画の始端。
        end: 計画の終端（継続中は ``None``）。
        adherence: 遵守率の事前（``planned`` の文の確信度）。
    """

    start: datetime
    end: datetime | None
    adherence: float


def build_evidence(
    inferred: Sequence[AssertionRow],
    planned: Sequence[AssertionRow],
    contributions: Sequence[ContributionRecord],
    *,
    start: datetime,
    end: datetime,
) -> tuple[tuple[EvidencePoint, ...], tuple[PlannedSpan, ...]]:
    """信念の点と計画を、区間 ``[start, end)`` で組み立てる。

    Args:
        inferred: 実体 × フルーエントの ``inferred`` の文（ABAC を通した後のもの）。
        planned: 同じ実体 × フルーエントの ``planned`` の文（同上）。
        contributions: 区間の寄与（``ContributionStore.series``）。
        start: 区間の始端（含む）。
        end: 区間の終端（含まない）。

    Returns:
        ``(信念の点を時刻順, 区間と重なる計画を開始順)``。
    """
    by_id: Mapping[UUID, ContributionRecord] = {item.assertion_id: item for item in contributions}
    points = [
        EvidencePoint(
            assertion_id=row.assertion_id,
            at=row.valid_from,
            probability=row.confidence,
            origin="coupling" if _COUPLING_MARK in row.provenance else "update",
            contribution=by_id.get(row.assertion_id),
        )
        for row in inferred
        if start <= row.valid_from < end
    ]
    points.sort(key=lambda point: (point.at, str(point.assertion_id)))
    spans = [
        PlannedSpan(start=row.valid_from, end=row.valid_to, adherence=row.confidence)
        for row in planned
        if row.valid_from < end and (row.valid_to is None or row.valid_to > start)
    ]
    spans.sort(key=lambda span: span.start)
    return tuple(points), tuple(spans)
