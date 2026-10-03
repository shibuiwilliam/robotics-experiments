"""照合エンジン（統合・抑制・トリアージ）の単体テスト（設計書 §8.2）。

重点:

1. **統合は抑制より先** — 先に抑制すると「半分だけ消えた不一致」が現場に見える。
2. **抑制は系統誤差が説明できる類型に限る** — 記録の遅れは状態の食い違いを説明しない。
3. **起票は深刻度順** — 順位付けが業務価値で決まる（推定の確からしさで決まらない）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sbp.core import vocab
from sbp.core.config import ReasoningSettings
from sbp.core.records import Record
from sbp.core.time import Interval
from sbp.integration.reliability import ReliabilityRegistry
from sbp.reasoning.reconciliation import (
    BusinessValue,
    BusinessValueRegistry,
    Discrepancy,
    DiscrepancyKind,
    DiscrepancyStatus,
    Severity,
    compute_severity,
    explain_as_systematic,
    merge_duplicates,
    reconcile,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
LINE = "https://sbp.example/site/siteA/line/L-1"
VERSION = vocab.ONTOLOGY_VERSION


@pytest.fixture(scope="module")
def reliability() -> ReliabilityRegistry:
    return ReliabilityRegistry.from_directory(REPO_ROOT / "reliability")


@pytest.fixture(scope="module")
def values() -> BusinessValueRegistry:
    return BusinessValueRegistry.from_directory(
        REPO_ROOT / "value", default_scale=ReasoningSettings().severity_value_scale
    )


def _candidate(
    *,
    kind: DiscrepancyKind = DiscrepancyKind.STATE,
    subject: str = PUMP,
    start: datetime = T0,
    minutes: int = 10,
    score: float = 0.5,
    evidence: tuple[str, ...] = (),
) -> Discrepancy:
    return Discrepancy(
        kind=kind,
        subjects=(subject,),
        evidence=evidence,
        detected_at=T0,
        interval=Interval(start, start + timedelta(minutes=minutes)),
        severity=Severity(
            score=score, value_at_stake=1000.0, confidence=0.9, persistence_s=minutes * 60
        ),
        detector="test",
        ontology_version=VERSION,
    )


def _late_work_order(*, lag: timedelta) -> Record:
    """CMMS の作業指示。遅れて計上された（月末一括計上の系統誤差に当たる）。"""
    return Record(
        record_type=vocab.ent.WorkOrder,
        source=vocab.ent.CmmsSource,
        external_id="WO-500",
        is_about=PUMP,
        recorded_at=T0 + lag,
        occurred_at=T0,
        ontology_version=VERSION,
    )


class TestMergeDuplicates:
    def test_overlapping_candidates_collapse(self) -> None:
        candidates = [
            _candidate(start=T0, evidence=("record:A",)),
            _candidate(start=T0 + timedelta(minutes=5), evidence=("record:B",)),
            _candidate(start=T0 + timedelta(minutes=8), evidence=("record:C",)),
        ]
        merged, absorbed = merge_duplicates(candidates, at=T0)
        assert len(merged) == 1
        assert absorbed == 2
        assert merged[0].evidence == ("record:A", "record:B", "record:C")

    def test_a_gap_splits_the_group(self) -> None:
        """間が空いたものは別の出来事。無理に 1 件にまとめない。"""
        candidates = [
            _candidate(start=T0, minutes=5),
            _candidate(start=T0 + timedelta(hours=4), minutes=5),
        ]
        merged, absorbed = merge_duplicates(candidates, at=T0)
        assert len(merged) == 2
        assert absorbed == 0

    def test_different_subjects_stay_separate(self) -> None:
        merged, _ = merge_duplicates([_candidate(subject=PUMP), _candidate(subject=LINE)], at=T0)
        assert len(merged) == 2

    def test_different_kinds_stay_separate(self) -> None:
        merged, _ = merge_duplicates(
            [
                _candidate(kind=DiscrepancyKind.STATE),
                _candidate(kind=DiscrepancyKind.TIME),
            ],
            at=T0,
        )
        assert len(merged) == 2


class TestSuppressionScope:
    """抑制は「系統誤差が説明できる類型」に限る（この縛りが無いと抑制が万能になる）。"""

    def test_a_late_record_suppresses_a_time_discrepancy(
        self, reliability: ReliabilityRegistry
    ) -> None:
        record = _late_work_order(lag=timedelta(days=20))
        candidate = _candidate(kind=DiscrepancyKind.TIME, evidence=("record:WO-500",))
        suppression = explain_as_systematic(
            candidate,
            records={"record:WO-500": record},
            reliability=reliability,
            at=T0,
        )
        assert suppression is not None
        assert suppression.bias_kind == "month_end_batch"
        assert "ent" in suppression.reliability_key or "sbp.example" in suppression.reliability_key

    def test_a_late_record_does_not_suppress_a_state_discrepancy(
        self, reliability: ReliabilityRegistry
    ) -> None:
        """記録が遅れて入ったことは、記録が別の状態を主張していることの説明にならない。"""
        record = _late_work_order(lag=timedelta(days=20))
        candidate = _candidate(kind=DiscrepancyKind.STATE, evidence=("record:WO-500",))
        assert (
            explain_as_systematic(
                candidate,
                records={"record:WO-500": record},
                reliability=reliability,
                at=T0,
            )
            is None
        )

    def test_a_discrepancy_without_record_evidence_cannot_be_suppressed(
        self, reliability: ReliabilityRegistry
    ) -> None:
        """純粋に物理観測だけで出た不一致は、記録の癖では説明できない。"""
        candidate = _candidate(kind=DiscrepancyKind.TIME, evidence=())
        assert explain_as_systematic(candidate, records={}, reliability=reliability, at=T0) is None

    def test_a_lag_beyond_the_bias_is_not_explained(self, reliability: ReliabilityRegistry) -> None:
        """月末一括計上は 31 日まで。半年遅れは別の問題である。"""
        record = _late_work_order(lag=timedelta(days=180))
        candidate = _candidate(kind=DiscrepancyKind.TIME, evidence=("record:WO-500",))
        assert (
            explain_as_systematic(
                candidate,
                records={"record:WO-500": record},
                reliability=reliability,
                at=T0,
            )
            is None
        )


class TestReconcileCycle:
    def test_the_cycle_merges_then_suppresses_then_sorts(
        self, reliability: ReliabilityRegistry
    ) -> None:
        explained = _candidate(
            kind=DiscrepancyKind.TIME, evidence=("record:WO-500",), start=T0, score=0.9
        )
        duplicate_of_explained = _candidate(
            kind=DiscrepancyKind.TIME,
            evidence=("record:WO-500",),
            start=T0 + timedelta(minutes=3),
            score=0.4,
        )
        low = _candidate(kind=DiscrepancyKind.STATE, score=0.2, start=T0)
        high = _candidate(kind=DiscrepancyKind.EXISTENCE, score=0.8, start=T0)

        outcome = reconcile(
            [explained, duplicate_of_explained, low, high],
            records={"record:WO-500": _late_work_order(lag=timedelta(days=20))},
            reliability=reliability,
            at=T0 + timedelta(hours=1),
        )
        assert outcome.merged_count == 1
        # 抑制は統合後の 1 件に対して起き、半分だけ残らない
        assert len(outcome.suppressed) == 1
        assert outcome.suppressed[0].status is DiscrepancyStatus.SUPPRESSED
        assert [d.severity.score for d in outcome.triaged] == [0.8, 0.2]
        assert all(d.status is DiscrepancyStatus.TRIAGED for d in outcome.triaged)
        assert outcome.total_detected == 4

    def test_without_reliability_nothing_is_suppressed(self) -> None:
        """信頼性モデルが無ければ抑制しない（説明できないものは抑制できない）。"""
        outcome = reconcile([_candidate(kind=DiscrepancyKind.TIME)], at=T0)
        assert outcome.suppressed == ()
        assert len(outcome.triaged) == 1


class TestSeverityModel:
    """深刻度は業務価値で順位が決まる（設計書 §8.2）。"""

    def test_business_value_dominates_equal_confidence(self, values: BusinessValueRegistry) -> None:
        line = compute_severity(
            value=values.get(vocab.ent.ProductionLine),
            confidence=0.9,
            persistence=timedelta(minutes=10),
            value_scale=values.scale,
            persistence_scale=timedelta(hours=1),
        )
        part = compute_severity(
            value=values.get(vocab.ent.SparePart),
            confidence=0.9,
            persistence=timedelta(minutes=10),
            value_scale=values.scale,
            persistence_scale=timedelta(hours=1),
        )
        assert line.score > part.score

    def test_safety_class_amplifies_equal_money(self) -> None:
        safe = BusinessValue(object_type=vocab.ent.Pump, asset_value=1000.0, safety_class=0.0)
        risky = BusinessValue(object_type=vocab.ent.Pump, asset_value=1000.0, safety_class=1.0)
        assert risky.value_at_stake() > safe.value_at_stake()

    def test_an_unregistered_type_is_not_ranked_last(self, values: BusinessValueRegistry) -> None:
        """設定漏れを「深刻でない」として黙殺しない。"""
        unknown = values.get("https://sbp.example/ent#NotRegistered")
        assert unknown.value_at_stake() > 0.0
        assert "未登録" in unknown.note

    def test_longer_persistence_scores_higher(self, values: BusinessValueRegistry) -> None:
        short = compute_severity(
            value=values.get(vocab.ent.Pump),
            confidence=0.8,
            persistence=timedelta(minutes=6),
            value_scale=values.scale,
            persistence_scale=timedelta(hours=1),
        )
        long = compute_severity(
            value=values.get(vocab.ent.Pump),
            confidence=0.8,
            persistence=timedelta(minutes=45),
            value_scale=values.scale,
            persistence_scale=timedelta(hours=1),
        )
        assert long.score > short.score

    def test_severity_is_not_a_probability(self, values: BusinessValueRegistry) -> None:
        """深刻度は順位付けの量であって確信度ではない（不変条件 7 と混ぜない）。"""
        severity = compute_severity(
            value=values.get(vocab.ent.ProductionLine),
            confidence=0.3,
            persistence=timedelta(hours=10),
            value_scale=values.scale,
            persistence_scale=timedelta(hours=1),
        )
        assert severity.confidence == pytest.approx(0.3)
        assert severity.score > severity.confidence
