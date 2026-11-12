"""照合エンジン（設計書 §8.2）。

五類型の検出規則を回し、**重複を統合し**、**系統誤差で説明できるものを抑制して**、残りを
起票する。順序はこの通りでなければならない:

1. 検出（各規則が独立に候補を出す）
2. 重複統合（同一実体・同一類型・区間重なり）
3. 抑制判定（信頼性モデルの系統誤差で説明できるか）
4. トリアージ（深刻度順）

統合を抑制より先に行うのは、「同じ食い違いが複数の規則・複数の刻みから出る」のが普通だから
である。抑制を先にやると、同じ事象の一部だけが抑制され残りが起票されて、現場には「半分だけ
消えた不一致」が見える。

抑制は**信頼性モデルで説明できる系統誤差に限る**（設計書 §8.2）。ここを緩めると照合エンジンは
「不都合な不一致を消す装置」になる。説明に使った系統誤差を :class:`Suppression` に必ず残し、
後から「この抑制は妥当だったか」を検証できるようにする。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sbp.core.logging import get_logger
from sbp.reasoning.reconciliation.discrepancy import (
    Discrepancy,
    DiscrepancyKind,
    DiscrepancyStatus,
    Suppression,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from sbp.core.records import Record
    from sbp.integration.reliability import ReliabilityRegistry, SystematicBias

__all__ = ["ReconciliationOutcome", "reconcile"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReconciliationOutcome:
    """照合 1 周分の結果。

    Attributes:
        triaged: 起票して人に渡す不一致（深刻度降順）。
        suppressed: 系統誤差として抑制した不一致。
        merged_count: 統合して消えた件数。
    """

    triaged: tuple[Discrepancy, ...]
    suppressed: tuple[Discrepancy, ...]
    merged_count: int

    @property
    def total_detected(self) -> int:
        """検出された延べ件数（統合前）。"""
        return len(self.triaged) + len(self.suppressed) + self.merged_count


def merge_duplicates(
    candidates: Sequence[Discrepancy], *, at: datetime
) -> tuple[list[Discrepancy], int]:
    """同一実体・同一類型・区間の重なる候補を統合する。

    Args:
        candidates: 検出規則が出した候補。
        at: 統合時刻。

    Returns:
        統合後の列と、統合で消えた件数。
    """
    grouped: dict[object, list[Discrepancy]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.merge_key, []).append(candidate)

    merged: list[Discrepancy] = []
    absorbed = 0
    for group in grouped.values():
        # 区間の開始順に並べ、重なる限り直前のものへ吸収していく
        ordered = sorted(group, key=lambda item: item.interval.start)
        current = ordered[0]
        for candidate in ordered[1:]:
            if current.interval.overlaps(candidate.interval):
                current = current.merge(candidate, at=at)
                absorbed += 1
            else:
                merged.append(current)
                current = candidate
        merged.append(current)
    return merged, absorbed


_BIAS_APPLIES_TO: dict[str, frozenset[DiscrepancyKind]] = {
    # 月末一括計上とシフト境界の丸めは「いつ記録したか」の偏りなので、説明できるのは時間不一致
    # だけである。記録が遅れて入ったことは、記録が**別の状態を主張している**ことの説明には
    # ならない。ここを緩めると「遅れて入った記録だから」で状態不一致まで消える。
    "month_end_batch": frozenset({DiscrepancyKind.TIME}),
    "shift_boundary": frozenset({DiscrepancyKind.TIME}),
    # 数量の丸めは数量の食い違い（存在不一致の数量版）を説明する。
    "quantity_rounding": frozenset({DiscrepancyKind.EXISTENCE}),
    # テンプレ複写は「前回の値がそのまま残る」偏りなので状態不一致を説明する。
    "template_copy": frozenset({DiscrepancyKind.STATE}),
}
"""系統誤差の種別ごとに、抑制してよい不一致の類型（設計書 §8.2 の抑制規則の限定）。"""

_BIAS_MAGNITUDE_UNIT: dict[str, timedelta] = {
    # magnitude の単位は種別ごとに違う（reliability/*.yaml のコメントに書いてある通り）。
    # month_end_batch は日数、shift_boundary はシフト長（時間）。
    "month_end_batch": timedelta(days=1),
    "shift_boundary": timedelta(hours=1),
}
"""時間不一致の抑制で、``magnitude`` をどの単位の時間として読むか。"""


def _bias_covers_the_deviation(bias: SystematicBias, discrepancy: Discrepancy) -> bool:
    """系統誤差の規模が、実際のずれの大きさを覆っているか。

    **偏りが存在することと、その偏りがこのずれを説明することは別である。** シフト境界の
    丸め（8 時間）は 6 時間のずれを説明しうるが、48 時間のずれは説明しない。規模を見ずに
    「既知の偏りがあるから」で抑制すると、どんな大きさのずれも消える。

    時間以外の類型（数量の丸めなど）はここで時間に換算できないため、規模の照合は行わず
    ``SystematicBias.explains`` の判定にゆだねる。

    Args:
        bias: 説明に使おうとしている系統誤差。
        discrepancy: 判定対象の不一致。
    """
    if discrepancy.kind is not DiscrepancyKind.TIME:
        return True
    unit = _BIAS_MAGNITUDE_UNIT.get(bias.kind)
    if unit is None:
        return True
    deviation = discrepancy.interval.duration
    if deviation is None:
        # 終端の無い不一致はいつまで続くか分からない。規模で覆えたと主張しない
        return False
    return deviation <= bias.magnitude * unit


def explain_as_systematic(
    discrepancy: Discrepancy,
    *,
    records: Mapping[str, Record],
    reliability: ReliabilityRegistry,
    at: datetime,
) -> Suppression | None:
    """信頼性モデルの系統誤差で説明できるかを判定する。

    抑制には**三つの条件**が要る。(1) その系統誤差が当該類型を説明しうる種別であること
    （:data:`_BIAS_APPLIES_TO`）、(2) 当該記録が実際にその偏りに当てはまること
    （``SystematicBias.explains``）、(3) 偏りの**規模がずれの大きさを覆っている**こと
    （:func:`_bias_covers_the_deviation`）。どれか 1 つでも落とすと抑制が万能になる——
    (1) を落とせば「月末に入力する運用だから」で状態の食い違いまで消え、(3) を落とせば
    8 時間のシフト丸めが 48 時間のずれを説明してしまう。

    証拠に記録が無い不一致（純粋に物理観測だけで出たもの）は抑制できない——記録の癖で説明
    できないのだから、それは本物の食い違いである。

    Args:
        discrepancy: 判定対象の不一致。
        records: 証拠 IRI → 記録。
        reliability: 信頼性モデルのレジストリ。
        at: 判定時刻。

    Returns:
        説明できたときは抑制記録、できなければ ``None``。
    """
    for reference in discrepancy.evidence:
        record = records.get(reference)
        if record is None:
            continue
        model = reliability.for_record(record)
        for bias in model.systematic_biases:
            if discrepancy.kind not in _BIAS_APPLIES_TO.get(bias.kind, frozenset()):
                continue
            if not bias.explains(record):
                continue
            if not _bias_covers_the_deviation(bias, discrepancy):
                log.info(
                    "reconciliation.bias_too_small_to_explain",
                    discrepancy=str(discrepancy.discrepancy_id),
                    bias=bias.kind,
                    magnitude=bias.magnitude,
                )
                continue
            return Suppression(
                bias_kind=bias.kind,
                reliability_key=f"{model.source}|{model.record_type}",
                reason=(
                    f"{bias.description}（情報源 {model.source} の既知の系統誤差。"
                    f"規模 {bias.magnitude}）"
                ),
                suppressed_at=at,
            )
    return None


def reconcile(
    candidates: Sequence[Discrepancy],
    *,
    records: Mapping[str, Record] | None = None,
    reliability: ReliabilityRegistry | None = None,
    at: datetime,
) -> ReconciliationOutcome:
    """検出候補を統合・抑制・トリアージする。

    Args:
        candidates: 五類型の検出規則が出した候補。
        records: 証拠 IRI → 記録（抑制判定に使う）。
        reliability: 信頼性モデル（抑制判定に使う）。両方そろって初めて抑制を試みる。
        at: 照合時刻。

    Returns:
        起票分と抑制分。
    """
    merged, absorbed = merge_duplicates(candidates, at=at)

    triaged: list[Discrepancy] = []
    suppressed: list[Discrepancy] = []
    for candidate in merged:
        suppression = (
            explain_as_systematic(candidate, records=records, reliability=reliability, at=at)
            if records and reliability is not None
            else None
        )
        if suppression is not None:
            suppressed.append(
                candidate.transition(
                    DiscrepancyStatus.SUPPRESSED,
                    at=at,
                    note=suppression.reason,
                    suppression=suppression,
                )
            )
            continue
        triaged.append(
            candidate.transition(
                DiscrepancyStatus.TRIAGED,
                at=at,
                note=f"severity={candidate.severity.score:.3f}",
            )
        )

    triaged.sort(key=lambda item: item.severity.score, reverse=True)
    log.info(
        "reconciliation.cycle_complete",
        detected=len(candidates),
        merged=absorbed,
        triaged=len(triaged),
        suppressed=len(suppressed),
    )
    return ReconciliationOutcome(
        triaged=tuple(triaged), suppressed=tuple(suppressed), merged_count=absorbed
    )
