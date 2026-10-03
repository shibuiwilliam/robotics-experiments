"""区間代数の性質テスト（hypothesis）。CLAUDE.md テスト方針で必須の 1 つ。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from sbp.core.time import AllenRelation, Interval, allen_relation

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

offsets = st.integers(min_value=0, max_value=10_000)
durations = st.integers(min_value=1, max_value=10_000)


def _interval(start_offset: int, duration: int | None) -> Interval:
    start = _EPOCH + timedelta(seconds=start_offset)
    end = None if duration is None else start + timedelta(seconds=duration)
    return Interval(start, end)


intervals = st.builds(_interval, offsets, st.one_of(durations, st.none()))


@given(intervals, intervals)
def test_allen_relation_is_total_on_interval_pairs(x: Interval, y: Interval) -> None:
    """任意の区間対に対して 13 関係のいずれか 1 つが決まる（排他かつ網羅）。"""
    assert allen_relation(x, y) in set(AllenRelation)


@given(intervals, intervals)
def test_allen_relation_inverse_consistency(x: Interval, y: Interval) -> None:
    """``x r y`` ならば ``y r⁻¹ x``。"""
    assert allen_relation(y, x) is allen_relation(x, y).inverse()


@given(intervals)
def test_allen_relation_is_reflexively_equal(x: Interval) -> None:
    """同じ区間同士は EQUALS。"""
    assert allen_relation(x, x) is AllenRelation.EQUALS


@given(intervals, intervals)
def test_overlaps_is_symmetric(x: Interval, y: Interval) -> None:
    """重なりは対称関係。"""
    assert x.overlaps(y) == y.overlaps(x)


@given(intervals, intervals)
def test_intersection_exists_iff_overlaps(x: Interval, y: Interval) -> None:
    """共通部分が存在することと重なることは同値。"""
    assert (x.intersection(y) is not None) == x.overlaps(y)


@given(intervals, intervals)
def test_intersection_is_contained_in_both(x: Interval, y: Interval) -> None:
    """共通部分の各時刻は両区間に含まれる。"""
    common = x.intersection(y)
    if common is None:
        return
    assert x.contains_instant(common.start)
    assert y.contains_instant(common.start)


@given(intervals, intervals)
def test_intersection_is_commutative(x: Interval, y: Interval) -> None:
    """共通部分は可換。"""
    assert x.intersection(y) == y.intersection(x)


@given(offsets, durations)
def test_right_open_boundary(start_offset: int, duration: int) -> None:
    """右開区間は始端を含み終端を含まない（不変条件 8）。"""
    iv = _interval(start_offset, duration)
    assert iv.end is not None
    assert iv.contains_instant(iv.start)
    assert not iv.contains_instant(iv.end)
