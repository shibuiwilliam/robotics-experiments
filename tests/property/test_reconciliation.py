"""照合の性質テスト（hypothesis）。設計書 §8.2。

照合エンジンは「ノイズ発生器にならないこと」が最大の要求である。個別の例では確認しきれない
ため、ここでは**どんな入力でも壊れない性質**を押さえる:

1. τ 未満の区間は決して返らない（ちらつき耐性）。
2. 返る区間は互いに重ならず昇順（統合処理がこれを前提にする）。
3. 乖離尺度は対称で 0〜1、同一分布でのみ 0。
4. 重複統合は件数を失わない（統合数 + 残存数 = 入力数）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

from hypothesis import given
from hypothesis import strategies as st

from sbp.core.time import Interval
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    Severity,
    jensen_shannon_distance,
    merge_duplicates,
    sustained_runs,
)

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
_STEP = timedelta(seconds=10)
_SUBJECT = "https://sbp.example/site/siteA/asset/P-101"

flag_series = st.lists(st.booleans(), min_size=1, max_size=200)
probabilities = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
taus = st.integers(min_value=1, max_value=50)


def _stamps(count: int) -> list[datetime]:
    return [_EPOCH + index * _STEP for index in range(count)]


@given(flag_series, taus)
def test_no_run_is_shorter_than_tau(flags: list[bool], tau_steps: int) -> None:
    """τ 未満の持続は返らない。プローブのちらつきが不一致にならないことの保証。"""
    persistence = tau_steps * _STEP
    for run in sustained_runs(_stamps(len(flags)), flags, persistence=persistence):
        assert run.duration is not None
        assert run.duration >= persistence


@given(flag_series, taus)
def test_runs_are_disjoint_and_ordered(flags: list[bool], tau_steps: int) -> None:
    """返る区間は互いに重ならず昇順（統合処理の前提）。"""
    runs = sustained_runs(_stamps(len(flags)), flags, persistence=tau_steps * _STEP)
    for earlier, later in pairwise(runs):
        assert earlier.upper_bound <= later.start
        assert not earlier.overlaps(later)


@given(flag_series)
def test_all_false_yields_nothing(flags: list[bool]) -> None:
    """条件が一度も成立しなければ何も返らない。"""
    negated = [False for _ in flags]
    assert sustained_runs(_stamps(len(flags)), negated, persistence=_STEP) == []


@given(probabilities, probabilities)
def test_divergence_is_symmetric_and_bounded(p: float, q: float) -> None:
    forward = jensen_shannon_distance(p, q)
    backward = jensen_shannon_distance(q, p)
    assert forward == backward
    assert 0.0 <= forward <= 1.0


@given(probabilities)
def test_divergence_is_zero_only_on_agreement(p: float) -> None:
    assert jensen_shannon_distance(p, p) < 1e-9


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=20),
            st.integers(min_value=1, max_value=10),
            st.sampled_from([DiscrepancyKind.EXISTENCE, DiscrepancyKind.STATE]),
        ),
        min_size=1,
        max_size=12,
    )
)
def test_merging_conserves_candidates(
    specs: list[tuple[int, int, DiscrepancyKind]],
) -> None:
    """統合で件数を失わない（統合数 + 残存数 = 入力数）。"""
    candidates = [
        Discrepancy(
            kind=kind,
            subjects=(_SUBJECT,),
            detected_at=_EPOCH,
            interval=Interval(_EPOCH + offset * _STEP, _EPOCH + (offset + length) * _STEP),
            severity=Severity(
                score=0.5, value_at_stake=1.0, confidence=0.5, persistence_s=float(length * 10)
            ),
            detector="property",
            ontology_version="0.3.0",
        )
        for offset, length, kind in specs
    ]
    merged, absorbed = merge_duplicates(candidates, at=_EPOCH)
    assert len(merged) + absorbed == len(candidates)
    assert 1 <= len(merged) <= len(candidates)


@given(
    st.lists(
        st.tuples(st.integers(min_value=0, max_value=20), st.integers(min_value=1, max_value=10)),
        min_size=1,
        max_size=12,
    )
)
def test_merged_results_of_one_key_are_disjoint(
    specs: list[tuple[int, int]],
) -> None:
    """同一鍵の統合結果は互いに重ならない（重なるならまだ統合できている）。"""
    candidates = [
        Discrepancy(
            kind=DiscrepancyKind.STATE,
            subjects=(_SUBJECT,),
            detected_at=_EPOCH,
            interval=Interval(_EPOCH + offset * _STEP, _EPOCH + (offset + length) * _STEP),
            severity=Severity(
                score=0.5, value_at_stake=1.0, confidence=0.5, persistence_s=float(length * 10)
            ),
            detector="property",
            ontology_version="0.3.0",
        )
        for offset, length in specs
    ]
    merged, _ = merge_duplicates(candidates, at=_EPOCH)
    for earlier, later in pairwise(merged):
        assert not earlier.interval.overlaps(later.interval)
