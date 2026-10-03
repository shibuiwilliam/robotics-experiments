"""時間の表現。不変条件 8 を型レベルで強制する。

- 時刻は timezone-aware UTC の :class:`datetime.datetime` のみ（float epoch・naive 禁止）。
- 期間は :class:`datetime.timedelta`。
- 区間は**右開** ``[start, end)``。``end=None`` は「現在も継続中（無限）」を表す。

Allen の 13 区間関係は :func:`allen_relation` で判定し、``holdsAt`` / ``occursDuring`` の
比較に用いる（設計書 §3.6）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator

from sbp.core.errors import TemporalError

__all__ = [
    "AllenRelation",
    "Interval",
    "UtcDatetime",
    "allen_relation",
    "ensure_utc",
    "now",
]


def ensure_utc(value: datetime) -> datetime:
    """timezone-aware UTC の datetime であることを検証し、UTC に正規化して返す。

    Args:
        value: 検証対象の datetime。

    Raises:
        TemporalError: naive datetime の場合（不変条件 8）。
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise TemporalError(
            f"naive datetime is forbidden (invariant 8): {value!r}. "
            "Use timezone-aware UTC, e.g. datetime(..., tzinfo=UTC)."
        )
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(ensure_utc)]
"""pydantic モデルで使う timezone-aware UTC の datetime 型。"""


def now() -> datetime:
    """現在時刻（timezone-aware UTC）。テストでは freezegun で固定する。"""
    return datetime.now(UTC)


class AllenRelation(StrEnum):
    """Allen の区間代数の 13 関係。``X <relation> Y`` の向きで読む。"""

    BEFORE = "before"
    """X が Y より前に終わり、接しない。"""
    MEETS = "meets"
    """X の終端が Y の始端に一致する。"""
    OVERLAPS = "overlaps"
    """X が Y に先行して始まり、重なって先に終わる。"""
    STARTS = "starts"
    """始端が一致し、X が先に終わる。"""
    DURING = "during"
    """X が Y に真に含まれる。"""
    FINISHES = "finishes"
    """終端が一致し、X が後に始まる。"""
    EQUALS = "equals"
    """始端・終端が一致する。"""
    FINISHED_BY = "finished_by"
    """:attr:`FINISHES` の逆。"""
    CONTAINS = "contains"
    """:attr:`DURING` の逆。"""
    STARTED_BY = "started_by"
    """:attr:`STARTS` の逆。"""
    OVERLAPPED_BY = "overlapped_by"
    """:attr:`OVERLAPS` の逆。"""
    MET_BY = "met_by"
    """:attr:`MEETS` の逆。"""
    AFTER = "after"
    """:attr:`BEFORE` の逆。"""

    def inverse(self) -> AllenRelation:
        """逆関係を返す（``X r Y`` ⟺ ``Y r⁻¹ X``）。"""
        return _ALLEN_INVERSE[self]


_ALLEN_INVERSE: dict[AllenRelation, AllenRelation] = {
    AllenRelation.BEFORE: AllenRelation.AFTER,
    AllenRelation.AFTER: AllenRelation.BEFORE,
    AllenRelation.MEETS: AllenRelation.MET_BY,
    AllenRelation.MET_BY: AllenRelation.MEETS,
    AllenRelation.OVERLAPS: AllenRelation.OVERLAPPED_BY,
    AllenRelation.OVERLAPPED_BY: AllenRelation.OVERLAPS,
    AllenRelation.STARTS: AllenRelation.STARTED_BY,
    AllenRelation.STARTED_BY: AllenRelation.STARTS,
    AllenRelation.DURING: AllenRelation.CONTAINS,
    AllenRelation.CONTAINS: AllenRelation.DURING,
    AllenRelation.FINISHES: AllenRelation.FINISHED_BY,
    AllenRelation.FINISHED_BY: AllenRelation.FINISHES,
    AllenRelation.EQUALS: AllenRelation.EQUALS,
}

_POSITIVE_INFINITY = datetime.max.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Interval:
    """右開区間 ``[start, end)``。``end=None`` は無限（現在も継続中）を表す。

    Args:
        start: 始端（この時刻を含む）。
        end: 終端（この時刻を含まない）。``None`` は無限。
    """

    start: datetime
    end: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", ensure_utc(self.start))
        if self.end is not None:
            object.__setattr__(self, "end", ensure_utc(self.end))
            if self.end <= self.start:
                raise TemporalError(
                    f"interval must be non-empty and right-open: start={self.start.isoformat()}, "
                    f"end={self.end.isoformat()} (end must be strictly after start)"
                )

    # ---- 基本性質 ----

    @property
    def is_open_ended(self) -> bool:
        """終端が未定（継続中）かどうか。"""
        return self.end is None

    @property
    def duration(self) -> timedelta | None:
        """区間の長さ。無限区間では ``None``。"""
        return None if self.end is None else self.end - self.start

    @property
    def upper_bound(self) -> datetime:
        """比較に使う終端。無限区間では ``datetime.max``（UTC）を返す。

        区間は右開なのでこの時刻自体は区間に含まれない。``allen_relation`` のように
        無限区間と有限区間を一様に比較したい場合に用いる。
        """
        return self.end if self.end is not None else _POSITIVE_INFINITY

    # ---- 包含・重なり ----

    def contains_instant(self, instant: datetime) -> bool:
        """時刻が区間に含まれるか（``start <= instant < end``）。"""
        t = ensure_utc(instant)
        return self.start <= t < self.upper_bound

    def overlaps(self, other: Interval) -> bool:
        """他の区間と共通部分を持つか（接するだけは重なりに含めない）。"""
        return self.start < other.upper_bound and other.start < self.upper_bound

    def intersection(self, other: Interval) -> Interval | None:
        """共通部分。重なりが無ければ ``None``。"""
        if not self.overlaps(other):
            return None
        start = max(self.start, other.start)
        upper = min(self.upper_bound, other.upper_bound)
        return Interval(start, None if upper == _POSITIVE_INFINITY else upper)

    def close_at(self, instant: datetime) -> Self:
        """無限区間を指定時刻で閉じた新しい区間を返す（二時制の ``tx_to`` 閉鎖に使う）。"""
        return type(self)(self.start, ensure_utc(instant))

    # ---- 表示 ----

    def __str__(self) -> str:
        end = "∞" if self.end is None else self.end.isoformat()
        return f"[{self.start.isoformat()}, {end})"


def allen_relation(x: Interval, y: Interval) -> AllenRelation:  # noqa: PLR0911
    """区間 ``x`` の ``y`` に対する Allen 関係を返す。

    右開区間として扱うため、``x.end == y.start`` は :attr:`AllenRelation.MEETS`（重なりなし）。

    Args:
        x: 左辺の区間。
        y: 右辺の区間。
    """
    xs, xe = x.start, x.upper_bound
    ys, ye = y.start, y.upper_bound

    # 接するか離れている場合（共通部分なし）
    if xe < ys:
        return AllenRelation.BEFORE
    if xe == ys:
        return AllenRelation.MEETS
    if xs > ye:
        return AllenRelation.AFTER
    if xs == ye:
        return AllenRelation.MET_BY

    # ここから共通部分あり（xs < ye かつ ys < xe）。始端と終端の大小で 9 通りに分かれる。
    if xs == ys:
        if xe < ye:
            return AllenRelation.STARTS
        if xe == ye:
            return AllenRelation.EQUALS
        return AllenRelation.STARTED_BY
    if xs < ys:
        if xe < ye:
            return AllenRelation.OVERLAPS
        if xe == ye:
            return AllenRelation.FINISHED_BY
        return AllenRelation.CONTAINS
    # ys < xs
    if xe < ye:
        return AllenRelation.DURING
    if xe == ye:
        return AllenRelation.FINISHES
    return AllenRelation.OVERLAPPED_BY
