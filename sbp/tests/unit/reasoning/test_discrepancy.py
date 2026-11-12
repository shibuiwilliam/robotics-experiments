"""不一致オブジェクトと状態機械の単体テスト（設計書 §8.2、付録 A.2）。

重点:

1. **状態名だけ進んだチケットを作れない** — 各状態はその状態で必要な中身を要求する。
2. **抑制には根拠が要る** — 系統誤差の参照と理由なしに ``Suppressed`` にできない。
3. **統合は証拠を捨てない** — 重複統合で区間が広がり、証拠が足される。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sbp.core.errors import DiscrepancyTransitionError
from sbp.core.time import Interval
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    DiscrepancyStatus,
    LearningSignals,
    Resolution,
    ResolutionOutcome,
    Severity,
    Suppression,
)

T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
ROLE = "https://sbp.example/role/maintenance-planner"


def _severity(score: float = 0.5) -> Severity:
    return Severity(score=score, value_at_stake=1000.0, confidence=0.9, persistence_s=600.0)


def _open(
    *,
    kind: DiscrepancyKind = DiscrepancyKind.STATE,
    start: datetime = T0,
    minutes: int = 10,
    score: float = 0.5,
    evidence: tuple[str, ...] = ("record:A",),
    subjects: tuple[str, ...] = (PUMP,),
) -> Discrepancy:
    return Discrepancy(
        kind=kind,
        subjects=subjects,
        evidence=evidence,
        detected_at=T0,
        interval=Interval(start, start + timedelta(minutes=minutes)),
        severity=_severity(score),
        detector="test",
        ontology_version="0.3.0",
    )


def _resolution(outcome: ResolutionOutcome = ResolutionOutcome.PHYSICAL_CORRECT) -> Resolution:
    return Resolution(
        outcome=outcome,
        note="現場確認済み",
        resolved_by="https://sbp.example/person/op-7",
        resolved_at=T0 + timedelta(hours=1),
    )


class TestLifecycle:
    """設計書 §8.2 のライフサイクル図に書かれた遷移だけを許す。"""

    def test_the_happy_path_runs_to_learned(self) -> None:
        discrepancy = _open()
        triaged = discrepancy.transition(DiscrepancyStatus.TRIAGED, at=T0)
        assigned = triaged.transition(DiscrepancyStatus.ASSIGNED, at=T0, assignee_role=ROLE)
        resolved = assigned.transition(DiscrepancyStatus.RESOLVED, at=T0, resolution=_resolution())
        learned = resolved.transition(
            DiscrepancyStatus.LEARNED,
            at=T0,
            learning=LearningSignals(reliability_updates=("ent:MesSource|ent:OperationLog",)),
        )
        assert [change.to_status for change in learned.history] == [
            DiscrepancyStatus.TRIAGED,
            DiscrepancyStatus.ASSIGNED,
            DiscrepancyStatus.RESOLVED,
            DiscrepancyStatus.LEARNED,
        ]
        assert learned.status.is_terminal
        assert not learned.is_actionable

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (DiscrepancyStatus.OPEN, DiscrepancyStatus.ASSIGNED),
            (DiscrepancyStatus.OPEN, DiscrepancyStatus.RESOLVED),
            (DiscrepancyStatus.OPEN, DiscrepancyStatus.LEARNED),
            (DiscrepancyStatus.TRIAGED, DiscrepancyStatus.SUPPRESSED),
            (DiscrepancyStatus.TRIAGED, DiscrepancyStatus.RESOLVED),
        ],
    )
    def test_shortcuts_are_rejected(
        self, source: DiscrepancyStatus, target: DiscrepancyStatus
    ) -> None:
        """段階を飛ばせない。飛ばせると「誰も見ていない解決済み」が作れる。"""
        discrepancy = _open()
        if source is DiscrepancyStatus.TRIAGED:
            discrepancy = discrepancy.transition(DiscrepancyStatus.TRIAGED, at=T0)
        with pytest.raises(DiscrepancyTransitionError, match="cannot move discrepancy"):
            discrepancy.transition(target, at=T0)

    def test_terminal_states_are_terminal(self) -> None:
        learned = (
            _open()
            .transition(DiscrepancyStatus.TRIAGED, at=T0)
            .transition(DiscrepancyStatus.ASSIGNED, at=T0, assignee_role=ROLE)
            .transition(DiscrepancyStatus.RESOLVED, at=T0, resolution=_resolution())
            .transition(
                DiscrepancyStatus.LEARNED, at=T0, learning=LearningSignals(labels_emitted=("x",))
            )
        )
        with pytest.raises(DiscrepancyTransitionError, match=r"\(terminal\)"):
            learned.transition(DiscrepancyStatus.TRIAGED, at=T0)

    def test_assigned_requires_a_role(self) -> None:
        triaged = _open().transition(DiscrepancyStatus.TRIAGED, at=T0)
        with pytest.raises(DiscrepancyTransitionError, match="must name an assignee_role"):
            triaged.transition(DiscrepancyStatus.ASSIGNED, at=T0)

    def test_resolved_requires_a_resolution(self) -> None:
        assigned = (
            _open()
            .transition(DiscrepancyStatus.TRIAGED, at=T0)
            .transition(DiscrepancyStatus.ASSIGNED, at=T0, assignee_role=ROLE)
        )
        with pytest.raises(DiscrepancyTransitionError, match="must carry its resolution"):
            assigned.transition(DiscrepancyStatus.RESOLVED, at=T0)

    def test_learned_requires_learning_signals(self) -> None:
        """学習信号を生まない解決は ``Resolved`` のままにする（設計書 §8.2 の表）。"""
        resolved = (
            _open()
            .transition(DiscrepancyStatus.TRIAGED, at=T0)
            .transition(DiscrepancyStatus.ASSIGNED, at=T0, assignee_role=ROLE)
            .transition(DiscrepancyStatus.RESOLVED, at=T0, resolution=_resolution())
        )
        with pytest.raises(DiscrepancyTransitionError, match="must carry the learning signals"):
            resolved.transition(DiscrepancyStatus.LEARNED, at=T0)
        with pytest.raises(DiscrepancyTransitionError, match="at least one learning signal"):
            resolved.transition(DiscrepancyStatus.LEARNED, at=T0, learning=LearningSignals())


class TestSuppression:
    """抑制は「不都合な不一致を消す手段」に転用できてはならない（設計書 §8.2）。"""

    def test_suppression_needs_its_evidence(self) -> None:
        discrepancy = _open()
        with pytest.raises(DiscrepancyTransitionError, match="must record why it was suppressed"):
            discrepancy.transition(DiscrepancyStatus.SUPPRESSED, at=T0)

    def test_suppression_records_the_bias_it_used(self) -> None:
        suppression = Suppression(
            bias_kind="month_end_batch",
            reliability_key="ent:CmmsSource|ent:WorkOrder",
            reason="月末一括入力",
            suppressed_at=T0,
        )
        suppressed = _open(kind=DiscrepancyKind.TIME).transition(
            DiscrepancyStatus.SUPPRESSED, at=T0, suppression=suppression
        )
        assert suppressed.suppression is not None
        assert suppressed.suppression.bias_kind == "month_end_batch"
        assert suppressed.status.is_terminal

    def test_suppression_cannot_be_built_without_a_reason(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            Suppression(
                bias_kind="month_end_batch",
                reliability_key="k",
                reason="",
                suppressed_at=T0,
            )


class TestMerge:
    """重複統合（設計書 §8.2）。同一実体・同一類型・区間重なりを 1 件に束ねる。"""

    def test_overlapping_duplicates_merge_and_keep_both_evidences(self) -> None:
        first = _open(start=T0, minutes=10, evidence=("record:A",), score=0.4)
        second = _open(
            start=T0 + timedelta(minutes=5), minutes=10, evidence=("record:B",), score=0.7
        )
        merged = first.merge(second, at=T0)
        assert merged.evidence == ("record:A", "record:B")
        assert merged.interval.start == T0
        assert merged.interval.end == T0 + timedelta(minutes=15)
        # 深刻度は高い方を採る（低い方に薄まると優先順位が狂う）
        assert merged.severity.score == pytest.approx(0.7)
        assert merged.history[-1].note.startswith("merged ")

    def test_merging_is_idempotent_on_evidence(self) -> None:
        first = _open(evidence=("record:A", "record:B"))
        second = _open(evidence=("record:B",))
        assert first.merge(second, at=T0).evidence == ("record:A", "record:B")

    def test_different_kinds_do_not_merge(self) -> None:
        with pytest.raises(DiscrepancyTransitionError, match="different keys"):
            _open(kind=DiscrepancyKind.STATE).merge(_open(kind=DiscrepancyKind.TIME), at=T0)

    def test_disjoint_intervals_do_not_merge(self) -> None:
        first = _open(start=T0, minutes=5)
        second = _open(start=T0 + timedelta(hours=3), minutes=5)
        with pytest.raises(DiscrepancyTransitionError, match="do not overlap"):
            first.merge(second, at=T0)

    def test_terminal_discrepancies_do_not_merge(self) -> None:
        """抑制済み・学習済みを後から統合すると、終端の意味が壊れる。"""
        suppression = Suppression(
            bias_kind="month_end_batch",
            reliability_key="k",
            reason="月末一括入力",
            suppressed_at=T0,
        )
        suppressed = _open().transition(
            DiscrepancyStatus.SUPPRESSED, at=T0, suppression=suppression
        )
        with pytest.raises(DiscrepancyTransitionError, match="terminal state"):
            suppressed.merge(_open(), at=T0)

    def test_merge_key_ignores_subject_order(self) -> None:
        a = _open(subjects=(PUMP, "https://sbp.example/x"))
        b = _open(subjects=("https://sbp.example/x", PUMP))
        assert a.merge_key == b.merge_key
