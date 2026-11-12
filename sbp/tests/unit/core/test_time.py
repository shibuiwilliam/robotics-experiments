"""時間表現の単体テスト（不変条件 8）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sbp.core.errors import TemporalError
from sbp.core.time import AllenRelation, Interval, allen_relation, ensure_utc

T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


class TestEnsureUtc:
    def test_naive_datetime_is_rejected(self) -> None:
        with pytest.raises(TemporalError, match="naive datetime is forbidden"):
            ensure_utc(datetime(2026, 9, 12, 9, 0))  # noqa: DTZ001

    def test_aware_non_utc_is_normalized_to_utc(self) -> None:
        jst = timezone(timedelta(hours=9))
        value = datetime(2026, 9, 12, 18, 0, tzinfo=jst)
        assert ensure_utc(value) == T0
        assert ensure_utc(value).tzinfo is UTC


class TestInterval:
    def test_right_open_excludes_end(self) -> None:
        iv = Interval(T0, T0 + HOUR)
        assert iv.contains_instant(T0)
        assert not iv.contains_instant(T0 + HOUR)

    def test_empty_interval_is_rejected(self) -> None:
        with pytest.raises(TemporalError, match="right-open"):
            Interval(T0, T0)

    def test_reversed_interval_is_rejected(self) -> None:
        with pytest.raises(TemporalError, match="right-open"):
            Interval(T0, T0 - HOUR)

    def test_open_ended_interval_contains_far_future(self) -> None:
        iv = Interval(T0)
        assert iv.is_open_ended
        assert iv.duration is None
        assert iv.contains_instant(T0 + timedelta(days=365 * 50))

    def test_touching_intervals_do_not_overlap(self) -> None:
        a = Interval(T0, T0 + HOUR)
        b = Interval(T0 + HOUR, T0 + 2 * HOUR)
        assert not a.overlaps(b)
        assert a.intersection(b) is None

    def test_intersection_of_overlapping_intervals(self) -> None:
        a = Interval(T0, T0 + 2 * HOUR)
        b = Interval(T0 + HOUR, T0 + 3 * HOUR)
        assert a.intersection(b) == Interval(T0 + HOUR, T0 + 2 * HOUR)

    def test_intersection_with_open_ended_stays_open(self) -> None:
        assert Interval(T0).intersection(Interval(T0 + HOUR)) == Interval(T0 + HOUR)

    def test_close_at_returns_new_closed_interval(self) -> None:
        iv = Interval(T0)
        closed = iv.close_at(T0 + HOUR)
        assert closed == Interval(T0, T0 + HOUR)
        assert iv.is_open_ended, "元の区間は不変であること"


class TestAllenRelation:
    @pytest.mark.parametrize(
        ("x", "y", "expected"),
        [
            (Interval(T0, T0 + HOUR), Interval(T0 + 2 * HOUR, T0 + 3 * HOUR), AllenRelation.BEFORE),
            (Interval(T0, T0 + HOUR), Interval(T0 + HOUR, T0 + 2 * HOUR), AllenRelation.MEETS),
            (
                Interval(T0, T0 + 2 * HOUR),
                Interval(T0 + HOUR, T0 + 3 * HOUR),
                AllenRelation.OVERLAPS,
            ),
            (Interval(T0, T0 + HOUR), Interval(T0, T0 + 2 * HOUR), AllenRelation.STARTS),
            (
                Interval(T0 + HOUR, T0 + 2 * HOUR),
                Interval(T0, T0 + 3 * HOUR),
                AllenRelation.DURING,
            ),
            (
                Interval(T0 + HOUR, T0 + 2 * HOUR),
                Interval(T0, T0 + 2 * HOUR),
                AllenRelation.FINISHES,
            ),
            (Interval(T0, T0 + HOUR), Interval(T0, T0 + HOUR), AllenRelation.EQUALS),
            (
                Interval(T0, T0 + 2 * HOUR),
                Interval(T0 + HOUR, T0 + 2 * HOUR),
                AllenRelation.FINISHED_BY,
            ),
            (
                Interval(T0, T0 + 3 * HOUR),
                Interval(T0 + HOUR, T0 + 2 * HOUR),
                AllenRelation.CONTAINS,
            ),
            (Interval(T0, T0 + 2 * HOUR), Interval(T0, T0 + HOUR), AllenRelation.STARTED_BY),
            (
                Interval(T0 + HOUR, T0 + 3 * HOUR),
                Interval(T0, T0 + 2 * HOUR),
                AllenRelation.OVERLAPPED_BY,
            ),
            (Interval(T0 + HOUR, T0 + 2 * HOUR), Interval(T0, T0 + HOUR), AllenRelation.MET_BY),
            (Interval(T0 + 2 * HOUR, T0 + 3 * HOUR), Interval(T0, T0 + HOUR), AllenRelation.AFTER),
        ],
    )
    def test_all_thirteen_relations(
        self, x: Interval, y: Interval, expected: AllenRelation
    ) -> None:
        assert allen_relation(x, y) is expected

    def test_relations_cover_all_thirteen(self) -> None:
        assert len(set(AllenRelation)) == 13

    def test_inverse_is_an_involution(self) -> None:
        for r in AllenRelation:
            assert r.inverse().inverse() is r
