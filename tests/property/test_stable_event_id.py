"""決定的な UUIDv7 の性質テスト（M8-25-3、ADR-0016 の問い 4）。

取込は at-least-once なので、同じ業務記録を読み直すことがある。読み直したイベントが同じ
``event_id`` を持てば、消費側の ``processed_events`` が弾く（不変条件 4: ``event_id`` で冪等）。
そのために ID を内容から導くが、**UUIDv7 の形を崩さない**（時刻部で並ぶ、版は 7）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from sbp.core.ids import event_id_timestamp_ms, stable_event_id

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _instant(millis: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=millis)


instants = st.builds(_instant, st.integers(min_value=0, max_value=10 * 365 * 24 * 3600 * 1000))
keys = st.text(min_size=1, max_size=200)


@given(instants, keys)
def test_the_same_input_gives_the_same_id(at: datetime, key: str) -> None:
    """読み直しても同じ ID になる（冪等の前提）。"""
    assert stable_event_id(at, key) == stable_event_id(at, key)


@given(instants, keys)
def test_the_id_is_a_uuid7(at: datetime, key: str) -> None:
    """版は 7、変種は RFC 9562（不変条件 4 の形式を保つ）。"""
    event_id = stable_event_id(at, key)
    assert event_id.version == 7
    assert event_id.variant == "specified in RFC 4122"


@given(instants, keys)
def test_the_time_part_is_the_given_instant(at: datetime, key: str) -> None:
    """時刻部は渡した時刻のミリ秒（ログを ID 順に並べると時刻順になる）。"""
    expected = int(at.timestamp() * 1000)
    assert event_id_timestamp_ms(stable_event_id(at, key)) == expected


@given(instants, keys, keys)
def test_different_keys_give_different_ids(at: datetime, first: str, second: str) -> None:
    """鍵が違えば ID も違う（ハッシュの衝突は無視できる）。"""
    if first != second:
        assert stable_event_id(at, first) != stable_event_id(at, second)
