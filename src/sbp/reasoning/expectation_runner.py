"""期待の周期検証の純粋な部分（M8-27-3、ADR-0017）。

learning-worker が期限切れの ``Active`` を引き、ここへ渡す。**永続化も時計も持たない**——入力は
呼び出し側が集めた観測、出力は「期待の次の状態」と「それに伴って書くもの」である。

**期限切れだけを検証する。** :func:`~sbp.reasoning.expectation.verify_expectation` は期限前でも
観測が無ければ ``Undetermined`` にする（期限を見るのは ``expire`` の分岐だけ）。周期ジョブが
全 ``Active`` を回すと、「まだ検証できない」が「判定不能」として記録される（所見 118）。
期限前に渡されたら ``None`` を返して触らない。

**記号側の「無い」は違反である。** 記録の存在は取込が投影する（``business_records``）ので、期限を
過ぎて記録が 1 件も無ければ、期待した業務効果は起きていない——観測不能ではなく外れである。
物理側（信念）は逆で、信念が無ければ観測不能（``None``）のまま ``Undetermined`` になり、
観測要求になる。

結果に伴うもの:

| 結果 | 書くもの |
|---|---|
| ``Violated`` | 不一致（記号側が外れ → 類型 1、そうでなければ類型 2）。ID は期待から決まる |
| ``Undetermined`` | 観測できなかった物理効果ごとの観測要求（記録型は要求にしない） |
| ``Fulfilled`` | 無し |
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

from sbp.core.logging import get_logger
from sbp.core.time import Interval
from sbp.knowledge.observations import ObservationRow
from sbp.reasoning.expectation import (
    Expectation,
    ExpectationStatus,
    VerificationInputs,
    verify_expectation,
)
from sbp.reasoning.reconciliation.discrepancy import Discrepancy, DiscrepancyKind

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping
    from datetime import datetime

    from sbp.reasoning.reconciliation.severity import Severity

    SeverityFor = Callable[[Expectation], Severity]
    """``期待 -> 深刻度``。対象の型の業務価値を引くのは呼び出し側（型は知識グラフにある）。"""

__all__ = [
    "DETECTOR",
    "ExpectationVerdict",
    "discrepancy_id_for",
    "expectation_evidence",
    "verify_if_overdue",
]

log = get_logger(__name__)

DETECTOR = "expectation"
"""期待の検証が起票した不一致の ``detector``。"""


@dataclass(frozen=True, slots=True)
class ExpectationVerdict:
    """検証 1 件の結果と、それに伴って書くもの。

    Attributes:
        expectation: 検証後の期待（``Fulfilled`` / ``Violated`` / ``Undetermined``）。
        discrepancy: 起票する不一致（``Violated`` のときだけ）。
        observation_requests: 積む観測要求（``Undetermined`` のときだけ）。
    """

    expectation: Expectation
    discrepancy: Discrepancy | None = None
    observation_requests: tuple[ObservationRow, ...] = ()


def discrepancy_id_for(expectation_id: UUID) -> UUID:
    """期待から決まる不一致の ID（同じ期待からは同じ不一致になる）。

    Args:
        expectation_id: 期待の識別子。
    """
    return uuid5(NAMESPACE_URL, f"urn:sbp:expectation:{expectation_id}:discrepancy")


def expectation_evidence(expectation_id: UUID) -> str:
    """不一致の証拠に入れる期待の IRI。

    Args:
        expectation_id: 期待の識別子。
    """
    return f"urn:sbp:expectation:{expectation_id}"


def verify_if_overdue(
    expectation: Expectation,
    *,
    beliefs: Mapping[str, float],
    present_records: Collection[str],
    at: datetime,
    severity_for: SeverityFor,
) -> ExpectationVerdict | None:
    """期限を過ぎた ``Active`` の期待を検証する。それ以外は触らない。

    Args:
        expectation: 期待。
        beliefs: 対象実体の信念（フルーエント型 IRI → 確率。``current_beliefs``）。
        present_records: 期待の作成後に対象へ記録のある記録型 IRI（``RecordIndex.present_types``）。
        at: 検証時刻。
        severity_for: 深刻度の算出（``Violated`` と ``Undetermined`` のときだけ呼ぶ）。

    Returns:
        検証結果。``Active`` でない・期限前なら ``None``（何も書かない）。
    """
    if expectation.status is not ExpectationStatus.ACTIVE or not expectation.is_overdue(at=at):
        return None

    symbolic_types = {effect.fluent for effect in expectation.effects_of("symbolic")}
    inputs = VerificationInputs(
        at=at,
        beliefs=dict(beliefs),
        records_present={
            record_type: record_type in present_records for record_type in symbolic_types
        },
    )
    verified = verify_expectation(expectation, inputs=inputs)

    if verified.status is ExpectationStatus.VIOLATED:
        return ExpectationVerdict(
            verified, discrepancy=_discrepancy(verified, severity=severity_for(verified))
        )
    if verified.status is ExpectationStatus.UNDETERMINED:
        return ExpectationVerdict(
            verified,
            observation_requests=_observation_requests(
                verified, beliefs=beliefs, severity=severity_for(verified)
            ),
        )
    return ExpectationVerdict(verified)


def _discrepancy(expectation: Expectation, *, severity: Severity) -> Discrepancy:
    """違反した期待を不一致にする。記号側が外れていれば類型 1、そうでなければ類型 2。"""
    assert expectation.verification is not None  # Violated は検証結果を持つ（モデルの不変条件）
    kind = (
        DiscrepancyKind.EXISTENCE
        if expectation.verification.symbolic_passed is False
        else DiscrepancyKind.STATE
    )
    deadline = expectation.deadline
    assert deadline is not None  # Active は効果を持つ
    return Discrepancy(
        discrepancy_id=discrepancy_id_for(expectation.expectation_id),
        kind=kind,
        subjects=(expectation.target,),
        evidence=(expectation_evidence(expectation.expectation_id),),
        detected_at=expectation.verification.verified_at,
        interval=Interval(expectation.created_at, deadline),
        severity=severity,
        detector=DETECTOR,
        ontology_version=expectation.ontology_version,
    )


def _observation_requests(
    expectation: Expectation, *, beliefs: Mapping[str, float], severity: Severity
) -> tuple[ObservationRow, ...]:
    """観測できなかった物理効果ごとの観測要求（記録型は要求にしない）。"""
    deadline = expectation.deadline
    assert deadline is not None
    interval = Interval(
        expectation.created_at, max(deadline, expectation.created_at + timedelta(seconds=1))
    )
    return tuple(
        ObservationRow(
            entity=effect.entity,
            fluent=effect.fluent,
            interval=interval,
            reason=f"期待の期限切れ: {effect.description or effect.fluent} を判定する観測が無い",
            utility=severity.score,
            discrepancy_id=f"expectation:{expectation.expectation_id}",
        )
        for effect in expectation.effects_of("rollout")
        if effect.fluent not in beliefs
    )
