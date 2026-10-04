"""期待と作業項目レコードの相互変換（M8-18）。

不一致の :mod:`sbp.reasoning.reconciliation.persistence` と同じ形である——``knowledge`` は
``Expectation`` の型を知らないので（下位層なので import できない）、この変換が両者を繋ぐ。

**期限は ``expected_effects`` の最小値である。** 期待ボードは「期限が近い順」で並べるので、
読み出しで計算すると並べ替えができない。列に出すために変換側で畳む。``Created`` は効果を
まだ持たないので期限が無い（``None``）——「期限切れが近い」判断の対象にならない。

**読み出しで検証する。** 表の ``payload`` は ``Expectation.model_dump(mode="json")`` で、
読むときに pydantic で検証し直す。省くと、古い版が書いた行が今の不変条件（状態と中身の整合）を
満たさないまま下流に流れる。
"""

from __future__ import annotations

from sbp.core.logging import get_logger
from sbp.knowledge.expectations import ExpectationRecord
from sbp.reasoning.expectation import Expectation

__all__ = ["from_record", "to_record"]

log = get_logger(__name__)


def to_record(expectation: Expectation) -> ExpectationRecord:
    """期待を表の行にする。

    Args:
        expectation: 期待。

    Returns:
        作業項目レコード。
    """
    deadlines = [effect.deadline for effect in expectation.expected_effects]
    return ExpectationRecord(
        expectation_id=expectation.expectation_id,
        status=expectation.status.value,
        status_rank=expectation.status.rank,
        action_type=expectation.action_type,
        target=expectation.target,
        created_at=expectation.created_at,
        # 効果が無ければ期限も無い（`Created` の状態）
        deadline=min(deadlines) if deadlines else None,
        payload=expectation.model_dump(mode="json"),
    )


def from_record(record: ExpectationRecord) -> Expectation:
    """表の行を期待に戻す。

    Args:
        record: 作業項目レコード。

    Returns:
        期待。

    Raises:
        ValueError: 列と本体の識別子が食い違う場合。**黙って本体を信じない**——どちらが
            正しいか分からない。
        pydantic.ValidationError: 本体が今の契約を満たさない場合。
    """
    restored = Expectation.model_validate(record.payload)
    if restored.expectation_id != record.expectation_id:
        log.error(
            "reasoning.expectation_record_mismatch",
            correlation_id=str(record.expectation_id),
            payload_id=str(restored.expectation_id),
            detail="列と本体の識別子が違う。書き込み側のバグ",
        )
        raise ValueError(
            f"record id {record.expectation_id} does not match payload id "
            f"{restored.expectation_id}; the row was written inconsistently"
        )
    return restored
