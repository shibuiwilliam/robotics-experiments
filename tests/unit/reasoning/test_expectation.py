"""期待検証器の単体テスト（設計書 §8.5、付録 A.3、M5-04）。

重点:

1. **期限切れは Violated ではなく Undetermined** — 証拠不足と期待外れを混同しない。
2. **記号側と潜在側を別々に判定する** — WM が外れたのか T が外れたのかを切り分ける。
3. **効果の無い期待を Active にできない** — 永遠に Active のまま検証も学習も起きない。
4. **判定方法を記録する** — 後から「なぜ違反と判定されたか」を再構成できる。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sbp.core import vocab
from sbp.core.errors import ReasoningError
from sbp.reasoning import (
    Expectation,
    ExpectationStatus,
    ExpectedEffect,
    VerificationInputs,
    VerificationMethod,
    verify_expectation,
)
from sbp.reasoning.expectation import bayes_factor, conformal_covered

T0 = datetime(2026, 9, 12, 10, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
VERSION = vocab.ONTOLOGY_VERSION


def _created() -> Expectation:
    return Expectation(
        action_event=uuid4(),
        action_type=vocab.ent.CreateEmergencyInspection,
        target=PUMP,
        created_at=T0,
        ontology_version=VERSION,
    )


def _symbolic_effect() -> ExpectedEffect:
    return ExpectedEffect(
        fluent=vocab.ent.WorkOrder,
        entity=PUMP,
        expected=1.0,
        lower=0.5,
        upper=1.0,
        deadline=T0 + timedelta(minutes=5),
        origin="symbolic",
        description="作業指示が起票される",
    )


def _rollout_effect() -> ExpectedEffect:
    return ExpectedEffect(
        fluent=vocab.ent.Inspected,
        entity=PUMP,
        expected=0.9,
        lower=0.8,
        upper=1.0,
        deadline=T0 + timedelta(hours=24),
        origin="rollout",
        description="点検済みになる",
    )


def _active() -> Expectation:
    return _created().activate((_symbolic_effect(), _rollout_effect()), at=T0)


class TestStatistics:
    def test_the_prediction_interval_is_right_closed(self) -> None:
        """時間区間（右開）と違い、予測区間の上端は「期待通り」側に入る。"""
        assert conformal_covered(1.0, lower=0.8, upper=1.0)
        assert conformal_covered(0.8, lower=0.8, upper=1.0)
        assert not conformal_covered(0.79, lower=0.8, upper=1.0)

    def test_an_inverted_interval_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="inverted"):
            conformal_covered(0.5, lower=0.9, upper=0.1)

    def test_the_bayes_factor_favours_the_alternative_when_observation_disagrees(self) -> None:
        agrees = bayes_factor(0.92, expected=0.9, alternative=0.1)
        disagrees = bayes_factor(0.05, expected=0.9, alternative=0.1)
        assert agrees < 1.0
        assert disagrees > 1.0

    def test_a_non_positive_scale_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="scale must be positive"):
            bayes_factor(0.5, expected=0.9, alternative=0.1, scale=0.0)


class TestExpectedEffect:
    def test_the_interval_must_contain_the_expectation(self) -> None:
        """区間が期待値を含まないと、アクションが成功しても違反になる。"""
        with pytest.raises(ReasoningError, match="outside its own interval"):
            ExpectedEffect(
                fluent=vocab.ent.Inspected,
                entity=PUMP,
                expected=0.9,
                lower=0.1,
                upper=0.5,
                deadline=T0,
                origin="rollout",
            )

    def test_business_effects_are_symbolic_and_physical_are_rollout(self) -> None:
        """設計書 §8.5: 記号側（T 由来）と潜在側（rollout 由来）を分けて検証する。"""
        assert _symbolic_effect().origin == "symbolic"
        assert _rollout_effect().origin == "rollout"


class TestLifecycle:
    def test_activation_requires_effects(self) -> None:
        """効果が無い期待は永遠に Active のままになる。"""
        with pytest.raises(ReasoningError, match="at least one expected effect"):
            _created().activate((), at=T0)

    def test_the_deadline_is_the_latest_effect_deadline(self) -> None:
        assert _active().deadline == T0 + timedelta(hours=24)

    def test_a_created_expectation_cannot_be_verified(self) -> None:
        with pytest.raises(ReasoningError, match="only an Active expectation"):
            verify_expectation(
                _created(), inputs=VerificationInputs(at=T0, beliefs={}, records_present={})
            )

    def test_learned_requires_stating_what_was_learned(self) -> None:
        """理由が無いと「学習済みの件数」が嘘になる。"""
        verified = verify_expectation(
            _active(),
            inputs=VerificationInputs(
                at=T0 + timedelta(hours=1),
                beliefs={vocab.ent.Inspected: 0.95},
                records_present={vocab.ent.WorkOrder: True},
            ),
        )
        with pytest.raises(ReasoningError, match="requires stating what was learned"):
            verified.mark_learned(at=T0, note="")

    def test_the_full_lifecycle_runs(self) -> None:
        verified = verify_expectation(
            _active(),
            inputs=VerificationInputs(
                at=T0 + timedelta(hours=1),
                beliefs={vocab.ent.Inspected: 0.95},
                records_present={vocab.ent.WorkOrder: True},
            ),
        )
        learned = verified.mark_learned(at=T0 + timedelta(hours=2), note="介入データとして登録")
        assert [entry[0] for entry in learned.history] == ["Active", "Fulfilled", "Learned"]
        assert learned.status.is_terminal

    def test_a_terminal_expectation_does_not_move(self) -> None:
        verified = verify_expectation(
            _active(),
            inputs=VerificationInputs(
                at=T0 + timedelta(hours=1),
                beliefs={vocab.ent.Inspected: 0.95},
                records_present={vocab.ent.WorkOrder: True},
            ),
        )
        learned = verified.mark_learned(at=T0, note="登録済み")
        with pytest.raises(ReasoningError, match="cannot move expectation"):
            learned.mark_learned(at=T0, note="二度目")


class TestVerdicts:
    def test_both_sides_passing_is_fulfilled(self) -> None:
        verified = verify_expectation(
            _active(),
            inputs=VerificationInputs(
                at=T0 + timedelta(hours=1),
                beliefs={vocab.ent.Inspected: 0.95},
                records_present={vocab.ent.WorkOrder: True},
            ),
        )
        assert verified.status is ExpectationStatus.FULFILLED
        assert verified.verification is not None
        assert verified.verification.symbolic_passed is True
        assert verified.verification.rollout_passed is True

    def test_only_the_rollout_failing_blames_the_world_model(self) -> None:
        """設計書 §8.5: WM とプロセスオントロジーのどちらが外れたかを切り分ける。"""
        verified = verify_expectation(
            _active(),
            inputs=VerificationInputs(
                at=T0 + timedelta(hours=1),
                beliefs={vocab.ent.Inspected: 0.1},
                records_present={vocab.ent.WorkOrder: True},
            ),
        )
        assert verified.status is ExpectationStatus.VIOLATED
        assert verified.verification is not None
        assert verified.verification.blames_the_world_model
        assert not verified.verification.blames_the_process_ontology

    def test_only_the_symbolic_side_failing_blames_the_process_ontology(self) -> None:
        verified = verify_expectation(
            _active(),
            inputs=VerificationInputs(
                at=T0 + timedelta(hours=1),
                beliefs={vocab.ent.Inspected: 0.95},
                records_present={vocab.ent.WorkOrder: False},
            ),
        )
        assert verified.status is ExpectationStatus.VIOLATED
        assert verified.verification is not None
        assert verified.verification.blames_the_process_ontology
        assert not verified.verification.blames_the_world_model

    def test_missing_evidence_before_the_deadline_is_undetermined(self) -> None:
        """期限前で観測が足りないなら、まだ判定できない。"""
        verified = verify_expectation(
            _active(),
            inputs=VerificationInputs(
                at=T0 + timedelta(minutes=1), records_present={vocab.ent.WorkOrder: True}
            ),
        )
        assert verified.status is ExpectationStatus.UNDETERMINED

    def test_an_overdue_expectation_is_undetermined_not_violated(self) -> None:
        """証拠不足を「アクションの失敗」として記録すると信頼性モデルと学習が歪む。"""
        verified = verify_expectation(
            _active(),
            inputs=VerificationInputs(
                at=T0 + timedelta(days=2), records_present={vocab.ent.WorkOrder: True}
            ),
        )
        assert verified.status is ExpectationStatus.UNDETERMINED
        assert verified.verification is not None
        assert "期限切れ" in verified.verification.note

    def test_a_refuted_face_is_a_violation_even_if_the_other_is_unobserved(self) -> None:
        """外れが分かっている期待を、期限切れだからと証拠不足にしない（所見 119）。"""
        verified = verify_expectation(
            _active(),
            inputs=VerificationInputs(
                at=T0 + timedelta(days=2), records_present={vocab.ent.WorkOrder: False}
            ),
        )
        assert verified.status is ExpectationStatus.VIOLATED

    def test_an_expectation_with_one_face_can_be_fulfilled_when_overdue(self) -> None:
        """効果を持たない面は「観測できない」ではなく「期待していない」（所見 119）。"""
        only_symbolic = _created().activate((_symbolic_effect(),), at=T0)
        verified = verify_expectation(
            only_symbolic,
            inputs=VerificationInputs(
                at=T0 + timedelta(days=2), records_present={vocab.ent.WorkOrder: True}
            ),
        )
        assert verified.status is ExpectationStatus.FULFILLED

    def test_the_method_is_recorded(self) -> None:
        verified = verify_expectation(
            _active(),
            inputs=VerificationInputs(
                at=T0 + timedelta(hours=1),
                beliefs={vocab.ent.Inspected: 0.95},
                records_present={vocab.ent.WorkOrder: True},
            ),
            method=VerificationMethod.BAYES_FACTOR,
        )
        assert verified.verification is not None
        assert verified.verification.method is VerificationMethod.BAYES_FACTOR
        assert verified.verification.statistic is not None

    def test_the_observations_are_kept(self) -> None:
        """後から「なぜその判定になったか」を再構成するため。"""
        verified = verify_expectation(
            _active(),
            inputs=VerificationInputs(
                at=T0 + timedelta(hours=1),
                beliefs={vocab.ent.Inspected: 0.42},
                records_present={vocab.ent.WorkOrder: True},
            ),
        )
        assert verified.verification is not None
        assert verified.verification.observations[vocab.ent.Inspected] == pytest.approx(0.42)
