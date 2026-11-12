"""前提検証・承認・実行・補償の単体テスト（設計書 §9.2、§9.3、M5-02・M5-03）。

重点:

1. **知らないことを「満たしている」としない** — 観測が無い設備にアクションが通らない。
2. **どの面で落ちたかが残る** — 人が上書きの判断をするのに必要。
3. **承認を飛ばして実行できない** — 段階の遷移が構造的に制限されている。
4. **上書きは理由なしにできない** — 理由の無い上書きはバグと区別できない。
5. **レート制限は対象ごと** — 別の設備には出せることを保つ。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sbp.actions import (
    ActionContract,
    ActionRegistry,
    ActionRequest,
    ActionStage,
    Approval,
    Override,
    RateLimitState,
    check_preconditions,
    expected_effect_for,
    require_satisfied,
)
from sbp.core import vocab
from sbp.core.errors import ActionError, ApprovalRequiredError, PreconditionNotMetError
from sbp.core.records import Record

REPO_ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 9, 12, 10, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
OTHER_PUMP = "https://sbp.example/site/siteA/asset/P-102"
LEAD = "https://sbp.example/person/lead-1"
OPERATOR = "https://sbp.example/person/op-7"
VERSION = vocab.ONTOLOGY_VERSION
GOOD_BELIEFS = {vocab.ent.Accessible: 0.9, vocab.ent.UnderMaintenance: 0.05}


@pytest.fixture(scope="module")
def registry() -> ActionRegistry:
    return ActionRegistry.from_directory(REPO_ROOT / "actions")


@pytest.fixture
def inspection(registry: ActionRegistry) -> ActionContract:
    return registry.get(vocab.ent.CreateEmergencyInspection)


def _work_order(status: str = "Open") -> Record:
    return Record(
        record_type=vocab.ent.WorkOrder,
        source=vocab.ent.CmmsSource,
        external_id="WO-1",
        is_about=PUMP,
        recorded_at=T0,
        occurred_at=T0,
        payload={"work_type": "Inspection", "status": status},
        ontology_version=VERSION,
    )


def _approval() -> Approval:
    return Approval(role=vocab.ent.MaintenanceLead, approved_by=LEAD, approved_at=T0)


class TestPreconditions:
    def test_all_three_faces_pass(self, inspection: ActionContract) -> None:
        report = check_preconditions(inspection, target=PUMP, beliefs=GOOD_BELIEFS)
        assert report.satisfied
        assert report.failed_faces() == ()
        assert "三面すべて満たしている" in report.explain()

    def test_an_unknown_belief_is_not_satisfied(self, inspection: ActionContract) -> None:
        """知らないことを満たしているとすると、観測が無い設備にアクションが通る。"""
        report = check_preconditions(inspection, target=PUMP, beliefs={})
        assert not report.satisfied
        assert "physical" in report.failed_faces()
        assert vocab.ent.Accessible in report.missing_evidence

    def test_missing_evidence_is_distinguished_from_a_failed_threshold(
        self, inspection: ActionContract
    ) -> None:
        """「未達」と「判定不能」は別物。前者は観測が足りている。"""
        below = check_preconditions(
            inspection, target=PUMP, beliefs={**GOOD_BELIEFS, vocab.ent.Accessible: 0.3}
        )
        assert not below.satisfied
        assert below.missing_evidence == ()
        assert any("閾値" in result.note for result in below.failures())

    def test_an_existing_open_work_order_blocks_a_second_one(
        self, inspection: ActionContract
    ) -> None:
        """二重起票しない（symbolic 面）。"""
        report = check_preconditions(
            inspection, target=PUMP, beliefs=GOOD_BELIEFS, records=[_work_order()]
        )
        assert not report.satisfied
        assert report.failed_faces() == ("symbolic",)

    def test_a_closed_work_order_does_not_block(self, inspection: ActionContract) -> None:
        report = check_preconditions(
            inspection, target=PUMP, beliefs=GOOD_BELIEFS, records=[_work_order(status="Closed")]
        )
        assert report.satisfied

    def test_a_record_about_another_entity_does_not_block(self, inspection: ActionContract) -> None:
        other = _work_order().model_copy(update={"is_about": OTHER_PUMP})
        report = check_preconditions(inspection, target=PUMP, beliefs=GOOD_BELIEFS, records=[other])
        assert report.satisfied

    def test_a_norm_violation_blocks_the_action(self, inspection: ActionContract) -> None:
        report = check_preconditions(
            inspection,
            target=PUMP,
            beliefs=GOOD_BELIEFS,
            norm_violations=["no_access_while_running"],
        )
        assert not report.satisfied
        assert report.failed_faces() == ("normative",)

    def test_multiple_faces_can_fail_at_once(self, inspection: ActionContract) -> None:
        report = check_preconditions(
            inspection,
            target=PUMP,
            beliefs={vocab.ent.Accessible: 0.1, vocab.ent.UnderMaintenance: 0.9},
            records=[_work_order()],
            norm_violations=["no_access_while_running"],
        )
        assert set(report.failed_faces()) == {"symbolic", "physical", "normative"}

    def test_require_satisfied_raises_with_the_explanation(
        self, inspection: ActionContract
    ) -> None:
        report = check_preconditions(inspection, target=PUMP, beliefs={})
        with pytest.raises(PreconditionNotMetError, match="前提が未達"):
            require_satisfied(report)


class TestFlowOrder:
    def test_the_happy_path_runs_to_executed(self, inspection: ActionContract) -> None:
        report = check_preconditions(inspection, target=PUMP, beliefs=GOOD_BELIEFS)
        request = ActionRequest(
            contract=inspection, target=PUMP, requested_by=OPERATOR, severity=0.8
        )
        assert request.check(report) is ActionStage.CHECKED
        assert request.approve(_approval()) is ActionStage.APPROVED
        event = request.execute(at=T0, executed_by=OPERATOR, rate_limits=RateLimitState())
        assert request.stage is ActionStage.EXECUTED
        assert event.command_id == request.command_id
        assert not event.was_overridden
        assert "三面すべて満たしている" in event.precondition_note

    def test_approval_cannot_be_skipped(self, inspection: ActionContract) -> None:
        """「開発中だけ飛ばす」経路を作ると、それが本番に残る。"""
        report = check_preconditions(inspection, target=PUMP, beliefs=GOOD_BELIEFS)
        request = ActionRequest(contract=inspection, target=PUMP, requested_by=OPERATOR)
        request.check(report)
        with pytest.raises(ActionError, match="cannot move"):
            request.execute(at=T0, executed_by=OPERATOR, rate_limits=RateLimitState())

    def test_the_check_cannot_be_skipped(self, inspection: ActionContract) -> None:
        request = ActionRequest(contract=inspection, target=PUMP, requested_by=OPERATOR)
        with pytest.raises(ActionError, match="cannot move"):
            request.approve(_approval())

    def test_a_rejected_request_is_terminal(self, inspection: ActionContract) -> None:
        report = check_preconditions(inspection, target=PUMP, beliefs={})
        request = ActionRequest(contract=inspection, target=PUMP, requested_by=OPERATOR)
        assert request.check(report) is ActionStage.REJECTED
        with pytest.raises(ActionError, match="cannot move"):
            request.approve(_approval())

    def test_a_report_for_another_target_is_refused(self, inspection: ActionContract) -> None:
        report = check_preconditions(inspection, target=OTHER_PUMP, beliefs=GOOD_BELIEFS)
        request = ActionRequest(contract=inspection, target=PUMP, requested_by=OPERATOR)
        with pytest.raises(ActionError, match="precondition report is for"):
            request.check(report)


class TestApproval:
    def test_medium_risk_without_approval_is_refused(self, inspection: ActionContract) -> None:
        report = check_preconditions(inspection, target=PUMP, beliefs=GOOD_BELIEFS)
        request = ActionRequest(contract=inspection, target=PUMP, requested_by=OPERATOR)
        request.check(report)
        with pytest.raises(ApprovalRequiredError, match="needs approval from"):
            request.approve(None)

    def test_the_wrong_role_cannot_approve(self, inspection: ActionContract) -> None:
        report = check_preconditions(inspection, target=PUMP, beliefs=GOOD_BELIEFS)
        request = ActionRequest(contract=inspection, target=PUMP, requested_by=OPERATOR)
        request.check(report)
        with pytest.raises(ApprovalRequiredError, match="must be approved by"):
            request.approve(
                Approval(role=vocab.ent.ProductionSupervisor, approved_by=LEAD, approved_at=T0)
            )

    def test_a_low_risk_action_needs_no_approval(self, registry: ActionRegistry) -> None:
        cancel = registry.get(vocab.ent.CancelWorkOrder)
        report = check_preconditions(cancel, target=PUMP, records=[_work_order()])
        request = ActionRequest(contract=cancel, target=PUMP, requested_by=OPERATOR)
        request.check(report)
        assert request.approve(None) is ActionStage.APPROVED


class TestOverride:
    def test_an_override_lets_a_failed_action_through(self, inspection: ActionContract) -> None:
        """上書きを禁じると現場が基盤を迂回し、基盤の外でアクションが実行される。"""
        report = check_preconditions(inspection, target=PUMP, beliefs={})
        override = Override(
            overridden_by=LEAD,
            overridden_at=T0,
            reason="現場で近接可能を目視確認した",
            failed_faces=report.failed_faces(),
        )
        request = ActionRequest(
            contract=inspection, target=PUMP, requested_by=OPERATOR, severity=0.9
        )
        assert request.check(report, override=override) is ActionStage.CHECKED
        request.approve(_approval())
        event = request.execute(at=T0, executed_by=OPERATOR, rate_limits=RateLimitState())
        assert event.was_overridden
        assert event.override is not None
        assert "目視確認" in event.override.reason

    def test_an_override_without_a_reason_cannot_be_built(self) -> None:
        with pytest.raises(ActionError, match="must state its reason"):
            Override(overridden_by=LEAD, overridden_at=T0, reason="", failed_faces=("physical",))

    def test_the_override_records_which_faces_it_bypassed(self, inspection: ActionContract) -> None:
        report = check_preconditions(
            inspection,
            target=PUMP,
            beliefs=GOOD_BELIEFS,
            norm_violations=["no_access_while_running"],
        )
        override = Override(
            overridden_by=LEAD,
            overridden_at=T0,
            reason="LOTO 実施済みで例外承認済み",
            failed_faces=report.failed_faces(),
        )
        assert override.failed_faces == ("normative",)


class TestRateLimit:
    def test_a_second_execution_within_the_window_is_refused(
        self, inspection: ActionContract
    ) -> None:
        limits = RateLimitState()
        report = check_preconditions(inspection, target=PUMP, beliefs=GOOD_BELIEFS)
        first = ActionRequest(contract=inspection, target=PUMP, requested_by=OPERATOR, severity=0.8)
        first.check(report)
        first.approve(_approval())
        first.execute(at=T0, executed_by=OPERATOR, rate_limits=limits)

        second = ActionRequest(
            contract=inspection, target=PUMP, requested_by=OPERATOR, severity=0.8
        )
        second.check(report)
        second.approve(_approval())
        with pytest.raises(ActionError, match="rate limited"):
            second.execute(at=T0 + timedelta(minutes=5), executed_by=OPERATOR, rate_limits=limits)

    def test_another_target_is_not_limited(self, inspection: ActionContract) -> None:
        """全体のスロットリングではなく対象ごと（別の設備には出せる）。"""
        limits = RateLimitState()
        first_report = check_preconditions(inspection, target=PUMP, beliefs=GOOD_BELIEFS)
        first = ActionRequest(contract=inspection, target=PUMP, requested_by=OPERATOR, severity=0.8)
        first.check(first_report)
        first.approve(_approval())
        first.execute(at=T0, executed_by=OPERATOR, rate_limits=limits)

        other_report = check_preconditions(inspection, target=OTHER_PUMP, beliefs=GOOD_BELIEFS)
        other = ActionRequest(
            contract=inspection, target=OTHER_PUMP, requested_by=OPERATOR, severity=0.8
        )
        other.check(other_report)
        other.approve(_approval())
        assert other.execute(at=T0 + timedelta(minutes=1), executed_by=OPERATOR, rate_limits=limits)

    def test_after_the_window_it_is_allowed_again(self, inspection: ActionContract) -> None:
        limits = RateLimitState()
        report = check_preconditions(inspection, target=PUMP, beliefs=GOOD_BELIEFS)
        first = ActionRequest(contract=inspection, target=PUMP, requested_by=OPERATOR, severity=0.8)
        first.check(report)
        first.approve(_approval())
        first.execute(at=T0, executed_by=OPERATOR, rate_limits=limits)

        later = ActionRequest(contract=inspection, target=PUMP, requested_by=OPERATOR, severity=0.8)
        later.check(report)
        later.approve(_approval())
        assert later.execute(at=T0 + timedelta(hours=2), executed_by=OPERATOR, rate_limits=limits)


class TestCompensation:
    def test_an_executed_action_can_be_compensated(self, inspection: ActionContract) -> None:
        report = check_preconditions(inspection, target=PUMP, beliefs=GOOD_BELIEFS)
        request = ActionRequest(
            contract=inspection, target=PUMP, requested_by=OPERATOR, severity=0.8
        )
        request.check(report)
        request.approve(_approval())
        request.execute(at=T0, executed_by=OPERATOR, rate_limits=RateLimitState())
        assert (
            request.compensate(at=T0 + timedelta(minutes=10), reason="誤起票のため取消し")
            is ActionStage.COMPENSATED
        )

    def test_compensation_needs_a_reason(self, inspection: ActionContract) -> None:
        report = check_preconditions(inspection, target=PUMP, beliefs=GOOD_BELIEFS)
        request = ActionRequest(
            contract=inspection, target=PUMP, requested_by=OPERATOR, severity=0.8
        )
        request.check(report)
        request.approve(_approval())
        request.execute(at=T0, executed_by=OPERATOR, rate_limits=RateLimitState())
        with pytest.raises(ActionError, match="must state its reason"):
            request.compensate(at=T0, reason="")

    def test_an_unexecuted_action_cannot_be_compensated(self, inspection: ActionContract) -> None:
        request = ActionRequest(contract=inspection, target=PUMP, requested_by=OPERATOR)
        with pytest.raises(ActionError, match="cannot move"):
            request.compensate(at=T0, reason="まだ実行していない")


class TestExpectationDeadlines:
    """効果ごとの期限（期待の生成は `tests/unit/actions/test_expectation_generation.py`）。

    **`planned_expectation_deadlines` を消した。** 期限の計算が `expected_effect_for` と
    2 箇所になり、同じことを違う形で計算する状態になっていた（所見 91）。期限の起点と
    加算は 1 箇所に置く。
    """

    def test_each_effect_gets_its_deadline_and_origin(self, inspection: ActionContract) -> None:
        """記号側（T 由来）と潜在側（rollout 由来）を別々に検証するため（設計書 §8.5）。"""
        by_origin = {
            effect.origin: expected_effect_for(
                effect, entity=PUMP, executed_at=T0, record_effect_floor=0.5
            ).deadline
            for effect in inspection.all_effects
        }
        assert by_origin["symbolic"] == T0 + timedelta(minutes=5)
        assert by_origin["rollout"] == T0 + timedelta(hours=24)
