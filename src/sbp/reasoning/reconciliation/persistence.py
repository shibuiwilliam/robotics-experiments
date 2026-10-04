"""不一致と作業項目レコードの相互変換（M8-12、ADR-0013）。

``knowledge`` は ``Discrepancy`` の型を知らない（下位層なので import できない）。この変換が
両者を繋ぐ——``bridge`` が ``knowledge`` を知らないので潜在索引の読み出しがワーカー側にある
のと同じ形で、**層の境界が変換関数の位置に出ている**。

**読み出しで検証する。** 表の ``payload`` は ``Discrepancy.model_dump(mode="json")`` で、
読むときに pydantic で検証し直す。検証を省くと、古い版が書いた行が今の不変条件
（状態と中身の整合など）を満たさないまま下流に流れる。
"""

from __future__ import annotations

from sbp.core.logging import get_logger
from sbp.knowledge.discrepancies import DiscrepancyRecord
from sbp.reasoning.reconciliation.discrepancy import Discrepancy

__all__ = ["from_record", "to_record"]

log = get_logger(__name__)


def to_record(discrepancy: Discrepancy) -> DiscrepancyRecord:
    """不一致を表の行にする。

    Args:
        discrepancy: 不一致。

    Returns:
        作業項目レコード。
    """
    return DiscrepancyRecord(
        discrepancy_id=discrepancy.discrepancy_id,
        kind=int(discrepancy.kind),
        status=discrepancy.status.value,
        status_rank=discrepancy.status.rank,
        subjects=discrepancy.subjects,
        severity_score=discrepancy.severity.score,
        detected_at=discrepancy.detected_at,
        payload=discrepancy.model_dump(mode="json"),
    )


def from_record(record: DiscrepancyRecord) -> Discrepancy:
    """表の行を不一致に戻す。

    **検証を通す。** 古い版が書いた行が今の不変条件を満たさないまま下流に流れるのを防ぐ
    ——`Discrepancy` の検証器は「状態名だけ進んだチケット」を拒否する。

    Args:
        record: 作業項目レコード。

    Returns:
        不一致。

    Raises:
        pydantic.ValidationError: 本体が今の契約を満たさない場合。
        DiscrepancyTransitionError: 状態と中身が整合しない場合（``Resolved`` なのに解決が
            無いなど）。
    """
    restored = Discrepancy.model_validate(record.payload)
    if restored.discrepancy_id != record.discrepancy_id:
        # 列と本体が食い違う行。**黙って本体を信じない**——どちらが正しいか分からない
        log.error(
            "reasoning.discrepancy_record_mismatch",
            correlation_id=str(record.discrepancy_id),
            payload_id=str(restored.discrepancy_id),
            detail="列と本体の識別子が違う。書き込み側のバグ",
        )
        raise ValueError(
            f"record id {record.discrepancy_id} does not match payload id "
            f"{restored.discrepancy_id}; the row was written inconsistently"
        )
    return restored
