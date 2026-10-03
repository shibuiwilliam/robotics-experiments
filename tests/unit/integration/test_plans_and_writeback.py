"""計画・規範の取込と書き戻しの単体テスト（設計書 §7.4、§7.6）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sbp.core import vocab
from sbp.core.belief import BooleanBelief
from sbp.core.config import WritebackMode
from sbp.core.errors import WritebackError
from sbp.core.modality import Modality
from sbp.core.records import Norm, NormSeverity, Record
from sbp.integration import (
    NormRule,
    WritebackCommand,
    WritebackExecutor,
    WritebackResult,
    WritebackTarget,
    plan_item_from_record,
    to_planned_assertion,
)
from sbp.integration.plans import ADHERENCE_PRIORS

T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
VERSION = vocab.ONTOLOGY_VERSION
PUMP = "https://sbp.example/site/siteA/asset/P-101"


def _plan_record(**payload: Any) -> Record:
    defaults: dict[str, Any] = {
        "plan_type": "PlannedOperation",
        "window_start": T0.isoformat().replace("+00:00", "Z"),
        "window_end": (T0 + timedelta(hours=8)).isoformat().replace("+00:00", "Z"),
    }
    return Record(
        record_type=vocab.ent.MaintenancePlan,
        source=vocab.ent.CmmsSource,
        external_id="MP-0912",
        is_about=PUMP,
        recorded_at=T0 - timedelta(days=7),
        payload=defaults | payload,
        ontology_version=VERSION,
    )


class TestPlanIngestion:
    def test_plan_becomes_planned_assertion_with_adherence_prior(self) -> None:
        """設計書 §14: 計画は遵守率 0.9 の planned アサーションになる。"""
        plan = plan_item_from_record(_plan_record(), intended_fluent=vocab.ent.Running)
        assert plan.adherence_prior == pytest.approx(ADHERENCE_PRIORS["PlannedOperation"])

        assertion = to_planned_assertion(plan, site_id="siteA", tx_from=T0)
        assert assertion.modality is Modality.PLANNED
        assert assertion.subject == PUMP
        assert assertion.predicate == vocab.ent.Running

        assert isinstance(assertion.belief, BooleanBelief)
        assert assertion.belief.p == pytest.approx(0.9)

    def test_planned_confidence_is_never_certain(self) -> None:
        """計画は「こうする予定」であって保証ではない（確信度 1.0 にしない）。"""
        for plan_type in ADHERENCE_PRIORS:
            plan = plan_item_from_record(
                _plan_record(plan_type=plan_type), intended_fluent=vocab.ent.Running
            )
            assert plan.adherence_prior < 1.0, f"{plan_type} の遵守率が 1.0 になっている"

    def test_valid_time_is_the_planned_interval(self) -> None:
        """有効時間は計画区間そのもの（右開）。"""
        plan = plan_item_from_record(_plan_record(), intended_fluent=vocab.ent.Running)
        assertion = to_planned_assertion(plan, site_id="siteA", tx_from=T0)
        assert assertion.valid_from == T0
        assert assertion.valid_to == T0 + timedelta(hours=8)
        assert assertion.holds_at(T0 + timedelta(hours=4))
        assert not assertion.holds_at(T0 + timedelta(hours=8)), "右開区間"

    def test_unknown_plan_type_gets_a_lower_prior(self) -> None:
        """知らない計画種は既知より低い遵守率にする（知らない計画を信用しない）。"""
        plan = plan_item_from_record(
            _plan_record(plan_type="SomethingNew"), intended_fluent=vocab.ent.Running
        )
        assert plan.adherence_prior < min(ADHERENCE_PRIORS.values())

    def test_missing_interval_is_rejected(self) -> None:
        record = _plan_record()
        broken = record.model_copy(update={"payload": {"plan_type": "PlannedOperation"}})
        with pytest.raises(ValueError, match="no planned interval"):
            plan_item_from_record(broken, intended_fluent=vocab.ent.Running)


class TestNormRule:
    def _norm(self, severity: NormSeverity) -> Norm:
        return Norm(
            norm_type=vocab.core.Norm,
            rule_ref="loto_required",
            source_document="https://sbp.example/site/siteA/doc/safety-manual",
            source_clause="§4.2",
            severity=severity,
            applies_to_type=vocab.ent.RotatingEquipment,
            effective_from=T0 - timedelta(days=365),
            ontology_version=VERSION,
        )

    def test_citation_points_at_the_source_clause(self) -> None:
        """規範違反は出典まで遡れる（監査性）。"""
        rule = NormRule(norm=self._norm(NormSeverity.CRITICAL), rule_name="norm_violation/3")
        assert rule.citation().endswith("#§4.2")

    def test_critical_norms_require_human_approval(self) -> None:
        assert NormRule(
            norm=self._norm(NormSeverity.CRITICAL), rule_name="r"
        ).requires_human_approval
        assert not NormRule(
            norm=self._norm(NormSeverity.ADVISORY), rule_name="r"
        ).requires_human_approval

    def test_severity_weights_are_ordered(self) -> None:
        """深刻度の重みは advisory < important < critical。"""
        weights = [
            s.weight for s in (NormSeverity.ADVISORY, NormSeverity.IMPORTANT, NormSeverity.CRITICAL)
        ]
        assert weights == sorted(weights)
        assert weights[-1] == 1.0

    def test_effectivity_window_is_right_open(self) -> None:
        norm = self._norm(NormSeverity.IMPORTANT).model_copy(
            update={"effective_from": T0, "effective_to": T0 + timedelta(days=1)}
        )
        assert norm.is_effective_at(T0)
        assert not norm.is_effective_at(T0 + timedelta(days=1))


class _RecordingWriter:
    """書き込みを記録するだけの writer（live モードの検証用）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[WritebackTarget, dict[str, Any]]] = []

    def write(self, target: WritebackTarget, changes: dict[str, Any]) -> str:
        self.calls.append((target, changes))
        return f"updated {target.table}/{target.external_id}"


class _FailingWriter:
    def write(self, target: WritebackTarget, changes: dict[str, Any]) -> str:
        del target, changes
        raise ConnectionError("business system unavailable")


def _command(**overrides: Any) -> WritebackCommand:
    defaults: dict[str, Any] = {
        "target": WritebackTarget(
            source=vocab.ent.MesSource, table="operation_log", external_id="OL-0912-A"
        ),
        "changes": {"running": 0},
        "compensation": "ent:RestoreOperationLog",
        "reason": "物理観測が停止を示したため MES 実績の訂正を提案する（設計書 §14）",
    }
    return WritebackCommand(**(defaults | overrides))


class TestWritebackSafety:
    def test_dry_is_the_default_and_does_not_write(self) -> None:
        """既定は dry。writer を渡さなくても構築でき、業務システムは変更されない。"""
        executor = WritebackExecutor()
        assert executor.mode is WritebackMode.DRY
        outcome = executor.execute(_command())
        assert outcome.result is WritebackResult.PLANNED
        assert not outcome.changed_the_business_system
        assert "not modified" in outcome.detail

    def test_dry_never_touches_the_writer(self) -> None:
        """dry では writer を呼ばない（「dry のつもりで書いた」事故を構造的に防ぐ）。"""
        writer = _RecordingWriter()
        executor = WritebackExecutor(mode=WritebackMode.DRY, writer=writer)
        executor.execute(_command())
        assert writer.calls == [], "dry モードで writer が呼ばれた"

    def test_live_requires_a_writer(self) -> None:
        with pytest.raises(WritebackError, match="requires a writer"):
            WritebackExecutor(mode=WritebackMode.LIVE)

    def test_live_applies_the_change(self) -> None:
        writer = _RecordingWriter()
        executor = WritebackExecutor(mode=WritebackMode.LIVE, writer=writer)
        outcome = executor.execute(_command())
        assert outcome.result is WritebackResult.APPLIED
        assert outcome.changed_the_business_system
        assert len(writer.calls) == 1
        assert writer.calls[0][1] == {"running": 0}

    def test_command_without_compensation_cannot_be_built(self) -> None:
        """取り消せない書き戻しは作れない（設計書 §7.6）。"""
        with pytest.raises(WritebackError, match="must declare its compensation"):
            _command(compensation="")

    def test_command_without_reason_cannot_be_built(self) -> None:
        with pytest.raises(WritebackError, match="must carry a human-readable reason"):
            _command(reason="")

    def test_empty_change_set_is_rejected(self) -> None:
        with pytest.raises(WritebackError, match="at least one field"):
            _command(changes={})


class TestWritebackIdempotency:
    def test_same_command_id_is_applied_once(self) -> None:
        """冪等: 再送しても二重に適用されない。"""
        writer = _RecordingWriter()
        executor = WritebackExecutor(mode=WritebackMode.LIVE, writer=writer)
        command = _command()
        first = executor.execute(command)
        second = executor.execute(command)
        assert first.result is WritebackResult.APPLIED
        assert second.result is WritebackResult.DUPLICATE
        assert len(writer.calls) == 1, "業務システムへの書き込みは 1 回だけ"

    def test_distinct_commands_are_each_applied(self) -> None:
        writer = _RecordingWriter()
        executor = WritebackExecutor(mode=WritebackMode.LIVE, writer=writer)
        executor.execute(_command())
        executor.execute(_command())
        assert len(writer.calls) == 2, "command_id が違えば別のコマンド"

    def test_failure_can_be_retried(self) -> None:
        """失敗したコマンドは冪等記録に入らない（再試行できる）。"""
        executor = WritebackExecutor(mode=WritebackMode.LIVE, writer=_FailingWriter())
        command = _command()
        first = executor.execute(command)
        assert first.result is WritebackResult.FAILED
        second = executor.execute(command)
        assert second.result is WritebackResult.FAILED, "DUPLICATE ではなく再試行される"


class TestCompensation:
    def test_applied_writeback_can_be_compensated(self) -> None:
        writer = _RecordingWriter()
        executor = WritebackExecutor(mode=WritebackMode.LIVE, writer=writer)
        outcome = executor.execute(_command())
        compensated = executor.compensate(outcome, changes={"running": 1})
        assert compensated.result is WritebackResult.APPLIED
        assert len(writer.calls) == 2
        assert writer.calls[1][1] == {"running": 1}

    def test_dry_run_cannot_be_compensated(self) -> None:
        """適用されていないものを取り消す必要はない。"""
        executor = WritebackExecutor()
        outcome = executor.execute(_command())
        with pytest.raises(WritebackError, match="cannot compensate"):
            executor.compensate(outcome, changes={"running": 1})
