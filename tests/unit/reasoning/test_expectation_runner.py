"""期待の周期検証の純粋な部分（M8-27-3、ADR-0017、所見 118）。

重点:

1. **期限前は触らない** — ``verify_expectation`` は期限前でも観測が無ければ
   ``Undetermined`` にする。
2. **記号側の「無い」は違反、物理側の「無い」は観測不能** — 前者は投影から断定でき、後者は信念が
   無いだけ。
3. **結果に応じて書くものが変わる** — 違反 → 不一致（類型 1 / 2）、判定不能 → 観測要求。
4. **同じ期待からは同じ不一致 ID** — 再実行で二重起票しない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sbp.core import vocab
from sbp.reasoning import Expectation, ExpectationStatus, ExpectedEffect
from sbp.reasoning.expectation_runner import (
    DETECTOR,
    discrepancy_id_for,
    expectation_evidence,
    verify_if_overdue,
)
from sbp.reasoning.reconciliation.discrepancy import DiscrepancyKind
from sbp.reasoning.reconciliation.severity import Severity

T0 = datetime(2026, 9, 12, 10, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
WORK_ORDER = str(vocab.ent.WorkOrder)
INSPECTED = str(vocab.ent.Inspected)
AFTER = T0 + timedelta(days=2)
SEVERITY = Severity(score=0.6, value_at_stake=1.0, confidence=0.9, persistence_s=60.0)


def _severity(_expectation: Expectation) -> Severity:
    return SEVERITY


def _effect(fluent: str, origin: str, hours: float) -> ExpectedEffect:
    return ExpectedEffect(
        fluent=fluent,
        entity=PUMP,
        expected=0.9,
        lower=0.8,
        upper=1.0,
        deadline=T0 + timedelta(hours=hours),
        origin=origin,  # type: ignore[arg-type]
        description=f"{fluent} の効果",
    )


def _active(*effects: ExpectedEffect) -> Expectation:
    created = Expectation(
        action_event=uuid4(),
        action_type=str(vocab.ent.CreateEmergencyInspection),
        target=PUMP,
        created_at=T0,
        ontology_version=vocab.ONTOLOGY_VERSION,
    )
    return created.activate(
        effects or (_effect(WORK_ORDER, "symbolic", 1), _effect(INSPECTED, "rollout", 24)), at=T0
    )


class TestOnlyOverdueIsVerified:
    def test_before_the_deadline_nothing_is_touched(self) -> None:
        expectation = _active()
        assert (
            verify_if_overdue(
                expectation,
                beliefs={},
                present_records=(),
                at=T0 + timedelta(hours=2),
                severity_for=_severity,
            )
            is None
        )

    def test_a_non_active_expectation_is_not_touched(self) -> None:
        done = _active().expire(at=AFTER)
        assert (
            verify_if_overdue(
                done, beliefs={}, present_records=(), at=AFTER, severity_for=_severity
            )
            is None
        )

    def test_exactly_at_the_deadline_is_not_overdue(self) -> None:
        expectation = _active()
        assert expectation.deadline is not None
        assert (
            verify_if_overdue(
                expectation,
                beliefs={},
                present_records=(),
                at=expectation.deadline,
                severity_for=_severity,
            )
            is None
        )


class TestVerdicts:
    def test_met_effects_are_fulfilled_and_write_nothing(self) -> None:
        verdict = verify_if_overdue(
            _active(),
            beliefs={INSPECTED: 0.95},
            present_records={WORK_ORDER},
            at=AFTER,
            severity_for=_severity,
        )
        assert verdict is not None
        assert verdict.expectation.status is ExpectationStatus.FULFILLED
        assert verdict.discrepancy is None
        assert verdict.observation_requests == ()

    def test_a_missing_record_is_a_violation_of_kind_existence(self) -> None:
        """記号側は投影から断定できる。期限を過ぎて記録が無ければ業務効果は起きていない。"""
        verdict = verify_if_overdue(
            _active(),
            beliefs={INSPECTED: 0.95},
            present_records=(),
            at=AFTER,
            severity_for=_severity,
        )
        assert verdict is not None
        assert verdict.expectation.status is ExpectationStatus.VIOLATED
        assert verdict.discrepancy is not None
        assert verdict.discrepancy.kind is DiscrepancyKind.EXISTENCE
        assert verdict.discrepancy.detector == DETECTOR
        assert verdict.discrepancy.subjects == (PUMP,)
        assert verdict.discrepancy.evidence == (
            expectation_evidence(verdict.expectation.expectation_id),
        )
        assert verdict.discrepancy.severity == SEVERITY

    def test_a_missed_physical_effect_is_a_violation_of_kind_state(self) -> None:
        verdict = verify_if_overdue(
            _active(),
            beliefs={INSPECTED: 0.1},
            present_records={WORK_ORDER},
            at=AFTER,
            severity_for=_severity,
        )
        assert verdict is not None
        assert verdict.expectation.status is ExpectationStatus.VIOLATED
        assert verdict.discrepancy is not None
        assert verdict.discrepancy.kind is DiscrepancyKind.STATE

    def test_no_belief_is_undetermined_and_asks_for_an_observation(self) -> None:
        """物理側の「無い」は観測不能。違反にせず、観測要求にする。"""
        verdict = verify_if_overdue(
            _active(),
            beliefs={},
            present_records={WORK_ORDER},
            at=AFTER,
            severity_for=_severity,
        )
        assert verdict is not None
        assert verdict.expectation.status is ExpectationStatus.UNDETERMINED
        assert verdict.discrepancy is None
        assert [(row.entity, row.fluent) for row in verdict.observation_requests] == [
            (PUMP, INSPECTED)
        ]
        row = verdict.observation_requests[0]
        assert row.utility == pytest.approx(SEVERITY.score)
        assert row.discrepancy_id == f"expectation:{verdict.expectation.expectation_id}"

    def test_record_types_are_never_observation_requests(self) -> None:
        """記録型はフルーエントではないので観測要求にしない（物理効果が無ければ空）。"""
        only_records = _active(_effect(WORK_ORDER, "symbolic", 1))
        verdict = verify_if_overdue(
            only_records,
            beliefs={},
            present_records={WORK_ORDER},
            at=AFTER,
            severity_for=_severity,
        )
        assert verdict is not None
        assert verdict.expectation.status is ExpectationStatus.FULFILLED
        assert verdict.observation_requests == ()


class TestDiscrepancyIdentity:
    def test_the_same_expectation_gives_the_same_discrepancy_id(self) -> None:
        expectation = _active()
        first = verify_if_overdue(
            expectation, beliefs={}, present_records=(), at=AFTER, severity_for=_severity
        )
        again = verify_if_overdue(
            expectation, beliefs={}, present_records=(), at=AFTER, severity_for=_severity
        )
        assert first is not None
        assert again is not None
        assert first.discrepancy is not None
        assert again.discrepancy is not None
        assert first.discrepancy.discrepancy_id == again.discrepancy.discrepancy_id
        assert first.discrepancy.discrepancy_id == discrepancy_id_for(expectation.expectation_id)

    def test_different_expectations_give_different_ids(self) -> None:
        assert discrepancy_id_for(uuid4()) != discrepancy_id_for(uuid4())
