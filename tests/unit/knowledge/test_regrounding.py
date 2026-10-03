"""再接地ジョブと版互換性行列の単体テスト（設計書 §6.8、§12.4、M7-01・M7-02）。

重点:

1. **コストを先に見積もる** — 無限スコープは拒否する（止められないジョブを作らない）。
2. **WM 版の昇格だけが潜在を無効化する** — プローブ 1 本で全潜在を再計算しない。
3. **旧文は閉じるだけ** — レポートに「削除 0 件」が出る（不変条件 2）。
4. **差分は人の確認を前提にする** — 自動採用しない。
5. **未検証の版の組み合わせを拒否する** — 4 つの版は独立に上げられない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sbp.core import vocab
from sbp.core.errors import RegroundingError
from sbp.core.time import Interval
from sbp.knowledge import (
    CompatibilityMatrix,
    DiffEntry,
    RegroundingPlan,
    RegroundingTrigger,
    plan_regrounding,
    summarise_regrounding,
)

T0 = datetime(2026, 10, 3, 10, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"


def _plan(**overrides: object) -> RegroundingPlan:
    values: dict[str, object] = {
        "trigger": RegroundingTrigger.PROBE_VERSION,
        "reason": "Running プローブを v2 に昇格",
        "entities": [PUMP],
        "fluents": [vocab.ent.Running],
        "interval": Interval(T0 - timedelta(hours=4), T0),
        "from_version": "probe/1.0.0",
        "to_version": "probe/2.0.0",
    }
    values.update(overrides)
    return plan_regrounding(**values)  # type: ignore[arg-type]


class TestTrigger:
    def test_only_a_world_model_bump_invalidates_latents(self) -> None:
        """プローブを 1 本足すたびに全潜在を再計算しない。"""
        assert RegroundingTrigger.WORLD_MODEL_VERSION.invalidates_latents
        assert not RegroundingTrigger.PROBE_VERSION.invalidates_latents
        assert not RegroundingTrigger.ONTOLOGY_VERSION.invalidates_latents
        assert not RegroundingTrigger.RETROSPECTIVE_INVESTIGATION.invalidates_latents


class TestPlanning:
    def test_the_estimate_scales_with_the_scope(self) -> None:
        small = _plan()
        large = _plan(
            entities=[f"{PUMP}-{index}" for index in range(50)],
            interval=Interval(T0 - timedelta(days=7), T0),
        )
        assert large.estimate.latents > small.estimate.latents * 100
        assert large.estimate.is_large
        assert not small.estimate.is_large

    def test_an_open_ended_interval_is_refused(self) -> None:
        """規模が分からないジョブは安全に止められない。"""
        with pytest.raises(RegroundingError, match="bounded interval"):
            _plan(interval=Interval(T0 - timedelta(hours=1)))

    def test_a_plan_must_state_its_reason(self) -> None:
        """理由が無いと、差分が「改善」か「新版の不具合」かを判断できない。"""
        with pytest.raises(RegroundingError, match="must state why it runs"):
            _plan(reason="")

    def test_an_empty_scope_is_refused(self) -> None:
        with pytest.raises(RegroundingError, match="must name the entities and fluents"):
            _plan(entities=[])

    def test_regrounding_to_the_same_version_is_refused(self) -> None:
        with pytest.raises(RegroundingError, match="does nothing"):
            _plan(from_version="probe/1.0.0", to_version="probe/1.0.0")

    def test_a_world_model_plan_says_latents_are_recomputed(self) -> None:
        plan = _plan(
            trigger=RegroundingTrigger.WORLD_MODEL_VERSION,
            from_version="wm/1.0.0",
            to_version="wm/2.0.0",
        )
        assert plan.estimate.recompute_latents
        assert "潜在の再計算" in plan.estimate.explain()

    def test_a_probe_plan_says_latents_are_reused(self) -> None:
        assert "潜在は再利用" in _plan().estimate.explain()

    def test_an_invalid_threshold_is_refused(self) -> None:
        with pytest.raises(RegroundingError, match="confidence_delta_threshold"):
            _plan(confidence_delta_threshold=0.0)


class TestDiffEntry:
    def test_a_new_statement_is_an_addition(self) -> None:
        entry = DiffEntry(
            subject=PUMP,
            predicate=vocab.ent.Running,
            valid_from=T0,
            old_confidence=None,
            new_confidence=0.9,
        )
        assert entry.is_new
        assert not entry.is_gone
        assert entry.delta == pytest.approx(0.9)

    def test_a_vanished_statement_is_a_removal(self) -> None:
        entry = DiffEntry(
            subject=PUMP,
            predicate=vocab.ent.Running,
            valid_from=T0,
            old_confidence=0.8,
            new_confidence=None,
        )
        assert entry.is_gone
        assert entry.delta == pytest.approx(-0.8)

    def test_an_entry_with_no_confidence_at_all_is_refused(self) -> None:
        with pytest.raises(RegroundingError, match="describes nothing"):
            DiffEntry(
                subject=PUMP,
                predicate=vocab.ent.Running,
                valid_from=T0,
                old_confidence=None,
                new_confidence=None,
            )

    def test_the_threshold_filters_small_changes(self) -> None:
        small = DiffEntry(
            subject=PUMP,
            predicate=vocab.ent.Running,
            valid_from=T0,
            old_confidence=0.50,
            new_confidence=0.52,
        )
        assert not small.exceeds(0.1)
        assert small.exceeds(0.01)


class TestReport:
    def _diffs(self) -> list[DiffEntry]:
        return [
            DiffEntry(
                subject=PUMP,
                predicate=vocab.ent.Running,
                valid_from=T0 - timedelta(hours=2),
                old_confidence=0.55,
                new_confidence=0.91,
            ),
            DiffEntry(
                subject=PUMP,
                predicate=vocab.ent.Accessible,
                valid_from=T0 - timedelta(hours=1),
                old_confidence=None,
                new_confidence=0.88,
            ),
            DiffEntry(
                subject=PUMP,
                predicate=vocab.ent.Inspected,
                valid_from=T0 - timedelta(hours=3),
                old_confidence=0.50,
                new_confidence=0.52,
            ),
        ]

    def test_only_notable_diffs_are_listed(self) -> None:
        report = summarise_regrounding(
            _plan(),
            diffs=self._diffs(),
            reassessed=1440,
            started_at=T0,
            finished_at=T0 + timedelta(minutes=3),
        )
        assert len(report.entries) == 2  # 0.02 の変化は閾値未満
        assert report.added == 1
        assert report.reassessed == 1440

    def test_the_total_reassessed_count_is_kept(self) -> None:
        """「1 万文を見て 3 文が動いた」と「3 文だけ見た」は全く違う情報である。"""
        report = summarise_regrounding(
            _plan(),
            diffs=self._diffs(),
            reassessed=10_000,
            started_at=T0,
            finished_at=T0 + timedelta(minutes=5),
        )
        assert "10,000 文" in report.markdown()

    def test_notable_diffs_require_review(self) -> None:
        """確信度が動いたのは改善か不具合か——自動で採用しない。"""
        report = summarise_regrounding(
            _plan(),
            diffs=self._diffs(),
            reassessed=1440,
            started_at=T0,
            finished_at=T0 + timedelta(minutes=3),
        )
        assert report.needs_review
        assert "人の確認が必要" in report.markdown()

    def test_no_notable_diffs_needs_no_review(self) -> None:
        tiny = [
            DiffEntry(
                subject=PUMP,
                predicate=vocab.ent.Running,
                valid_from=T0,
                old_confidence=0.50,
                new_confidence=0.51,
            )
        ]
        report = summarise_regrounding(
            _plan(),
            diffs=tiny,
            reassessed=100,
            started_at=T0,
            finished_at=T0 + timedelta(seconds=30),
        )
        assert not report.needs_review
        assert "確認は不要" in report.markdown()

    def test_the_report_states_that_nothing_was_deleted(self) -> None:
        """不変条件 2: 旧文は tx_to を閉じるだけ。"""
        report = summarise_regrounding(
            _plan(),
            diffs=self._diffs(),
            reassessed=1440,
            started_at=T0,
            finished_at=T0 + timedelta(minutes=3),
            closed_assertions=42,
        )
        markdown = report.markdown()
        assert "`tx_to` を閉じた 42 件" in markdown
        assert "削除は 0 件" in markdown

    def test_new_discrepancies_require_review_even_without_diffs(self) -> None:
        report = summarise_regrounding(
            _plan(),
            diffs=[],
            reassessed=100,
            started_at=T0,
            finished_at=T0 + timedelta(seconds=10),
            new_discrepancies=["d-1"],
        )
        assert report.needs_review

    def test_a_report_cannot_finish_before_it_starts(self) -> None:
        with pytest.raises(RegroundingError, match="finished before it started"):
            summarise_regrounding(
                _plan(),
                diffs=[],
                reassessed=0,
                started_at=T0,
                finished_at=T0 - timedelta(minutes=1),
            )

    def test_more_diffs_than_reassessed_is_refused(self) -> None:
        """数が合わないレポートを出さない。"""
        with pytest.raises(RegroundingError, match="counts do not add up"):
            summarise_regrounding(
                _plan(),
                diffs=self._diffs(),
                reassessed=1,
                started_at=T0,
                finished_at=T0 + timedelta(minutes=1),
            )

    def test_the_markdown_carries_the_version_change(self) -> None:
        report = summarise_regrounding(
            _plan(),
            diffs=[],
            reassessed=10,
            started_at=T0,
            finished_at=T0 + timedelta(seconds=5),
        )
        assert "probe/1.0.0 → probe/2.0.0" in report.markdown()


class TestCompatibilityMatrix:
    def test_a_verified_combination_passes(self) -> None:
        matrix = CompatibilityMatrix()
        matrix.record(ontology="0.5.0", world_model="koopman/1.0.0", probe="1.0.0", mapping="1.0.0")
        matrix.require_verified(
            ontology="0.5.0", world_model="koopman/1.0.0", probe="1.0.0", mapping="1.0.0"
        )

    def test_an_unverified_combination_is_refused(self) -> None:
        """4 つの版は独立に上げられない（設計書 §12.4）。"""
        matrix = CompatibilityMatrix()
        matrix.record(ontology="0.5.0", world_model="koopman/1.0.0", probe="1.0.0", mapping="1.0.0")
        with pytest.raises(RegroundingError, match="has not been verified"):
            matrix.require_verified(
                ontology="0.5.0", world_model="koopman/2.0.0", probe="1.0.0", mapping="1.0.0"
            )

    def test_impact_analysis_finds_affected_combinations(self) -> None:
        """影響分析: ある WM 版を上げたとき、どの組み合わせが再検証対象か。"""
        matrix = CompatibilityMatrix()
        matrix.record(ontology="0.5.0", world_model="koopman/1.0.0", probe="1.0.0", mapping="1.0.0")
        matrix.record(ontology="0.5.0", world_model="koopman/1.0.0", probe="2.0.0", mapping="1.0.0")
        matrix.record(ontology="0.5.0", world_model="jepa/1.0.0", probe="1.0.0", mapping="1.0.0")
        affected = matrix.affected_by(world_model="koopman/1.0.0")
        assert len(affected) == 2
        assert all(combination[1] == "koopman/1.0.0" for combination in affected)

    def test_recording_is_idempotent(self) -> None:
        matrix = CompatibilityMatrix()
        for _ in range(3):
            matrix.record(
                ontology="0.5.0", world_model="koopman/1.0.0", probe="1.0.0", mapping="1.0.0"
            )
        assert len(matrix.verified) == 1
