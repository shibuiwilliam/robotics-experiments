"""照合の入力となる信念時系列と「持続」の判定（設計書 §8.2）。

五類型の検出規則のうち 1・2・5 は「閾値を超えた状態が τ 以上**持続**した」ことを条件にする。
この τ が設計上の要点である。瞬間値で起票すると、プローブのちらつきと記録の遅延がそのまま
不一致になり、照合エンジンがノイズ発生器になる（設計書 §15 の失敗モード「不一致の洪水」）。

``BeliefSeries`` は実体 × フルーエントごとの事後確率の列を持つ。観測由来と記録由来を**別の列**
として持つのが要点で、類型 2 は「この 2 つがどれだけ食い違っているか」そのものだからである。
片方に畳んだ事後からは、もう一方との乖離を復元できない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

from sbp.core.errors import ReasoningError
from sbp.core.time import Interval, ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

__all__ = ["BeliefSeries", "jensen_shannon_distance", "sustained_runs"]

_LOG_EPSILON = 1e-12


def _entropy(p: float) -> float:
    """ベルヌーイ分布の 2 値エントロピー（bit）。"""
    q = 1.0 - p
    return -(p * math.log2(max(p, _LOG_EPSILON)) + q * math.log2(max(q, _LOG_EPSILON)))


def jensen_shannon_distance(p: float, q: float) -> float:
    """2 つのベルヌーイ分布の Jensen-Shannon 距離（設計書 §8.2 の類型 2）。

    発散（divergence、bit 単位で 0〜1）の平方根を取った**距離**を返す。距離を使うのは、
    閾値 θ_2=0.5 が「確率 0.5 ずれたら」という直感と対応するようにするためである
    （発散のままだと 0.5 は相当に大きな乖離になり、ほとんど発火しない）。

    Args:
        p: 一方の分布の成功確率。
        q: 他方の分布の成功確率。

    Returns:
        0（同一）〜1（完全に排反）の距離。
    """
    for name, value in (("p", p), ("q", q)):
        if not 0.0 <= value <= 1.0:
            raise ReasoningError(f"{name} must be a probability in [0, 1], got {value}")
    m = 0.5 * (p + q)
    divergence = _entropy(m) - 0.5 * (_entropy(p) + _entropy(q))
    return math.sqrt(max(divergence, 0.0))


def sustained_runs(
    timestamps: Sequence[datetime],
    flags: Sequence[bool],
    *,
    persistence: timedelta,
) -> list[Interval]:
    """条件が ``persistence`` 以上続いた区間を返す。

    区間は右開 ``[start, end)``（不変条件 8）。列の末尾まで条件が続いている場合は、最後の刻みの
    時刻を終端にする——「まだ続いているかもしれない」ことを無限区間で主張しないためである
    （観測していない先を主張しない）。

    Args:
        timestamps: 各刻みの時刻（昇順）。
        flags: 各刻みで条件が成立しているか。
        persistence: 必要な持続時間 τ。

    Returns:
        条件が τ 以上続いた区間の列。

    Raises:
        ReasoningError: 長さが合わない、または時刻が昇順でない場合。
    """
    if len(timestamps) != len(flags):
        raise ReasoningError(f"timestamps has {len(timestamps)} entries but flags has {len(flags)}")
    moments = [ensure_utc(t) for t in timestamps]
    for earlier, later in pairwise(moments):
        if later <= earlier:
            raise ReasoningError(
                f"timestamps must be strictly increasing: {earlier.isoformat()} then "
                f"{later.isoformat()}"
            )

    runs: list[Interval] = []
    start: int | None = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append(Interval(moments[start], moments[index]))
            start = None
    if start is not None and start < len(moments) - 1:
        runs.append(Interval(moments[start], moments[-1]))
    return [run for run in runs if (run.duration or persistence) >= persistence]


@dataclass(frozen=True, slots=True)
class BeliefSeries:
    """実体 × フルーエントの信念時系列（照合規則の入力）。

    Attributes:
        entity: 対象実体の IRI。
        fluent: フルーエント型の IRI。
        timestamps: 各刻みの時刻（昇順）。
        observed: 物理観測から導いた確率の列（プローブ由来の事後）。
        recorded: 業務記録が含意する確率の列。記録が無い刻みは ``None``。
    """

    entity: str
    fluent: str
    timestamps: tuple[datetime, ...]
    observed: tuple[float, ...]
    recorded: tuple[float | None, ...] = ()

    def __post_init__(self) -> None:
        """長さと値域を検証する。"""
        if len(self.timestamps) != len(self.observed):
            raise ReasoningError(
                f"series for {self.entity} has {len(self.timestamps)} timestamps but "
                f"{len(self.observed)} observed values"
            )
        if self.recorded and len(self.recorded) != len(self.timestamps):
            raise ReasoningError(
                f"series for {self.entity} has {len(self.timestamps)} timestamps but "
                f"{len(self.recorded)} recorded values"
            )
        for value in self.observed:
            if not 0.0 <= value <= 1.0:
                raise ReasoningError(f"observed probability out of range: {value}")
        for maybe in self.recorded:
            if maybe is not None and not 0.0 <= maybe <= 1.0:
                raise ReasoningError(f"recorded probability out of range: {maybe}")

    @property
    def has_records(self) -> bool:
        """記録由来の列を持つか（類型 2 の判定に必要）。"""
        return any(value is not None for value in self.recorded)

    def mean_observed(self, interval: Interval) -> float:
        """区間内の観測由来確率の平均（深刻度の確信度に使う）。

        Args:
            interval: 対象区間。
        """
        values = [
            value
            for at, value in zip(self.timestamps, self.observed, strict=True)
            if interval.contains_instant(at)
        ]
        if not values:
            raise ReasoningError(
                f"no samples of {self.entity} fall inside {interval.start.isoformat()}.."
            )
        return sum(values) / len(values)

    def runs_above(self, theta: float, *, persistence: timedelta) -> list[Interval]:
        """観測由来確率が ``theta`` 以上の状態が τ 以上続いた区間。

        Args:
            theta: 閾値。
            persistence: 必要な持続時間 τ。
        """
        return sustained_runs(
            self.timestamps, [value >= theta for value in self.observed], persistence=persistence
        )

    def runs_below(self, theta: float, *, persistence: timedelta) -> list[Interval]:
        """観測由来確率が ``theta`` 以下の状態が τ 以上続いた区間。

        Args:
            theta: 閾値。
            persistence: 必要な持続時間 τ。
        """
        return sustained_runs(
            self.timestamps, [value <= theta for value in self.observed], persistence=persistence
        )

    def runs_diverging(self, theta: float, *, persistence: timedelta) -> list[Interval]:
        """観測と記録の Jensen-Shannon 距離が ``theta`` 以上続いた区間（類型 2）。

        記録が無い刻みは「食い違っていない」とみなす（記録の不在は類型 1 の仕事である）。

        Args:
            theta: 距離の閾値 θ_2。
            persistence: 必要な持続時間 τ。
        """
        if not self.has_records:
            return []
        flags = [
            False if recorded is None else jensen_shannon_distance(observed, recorded) >= theta
            for observed, recorded in zip(self.observed, self.recorded, strict=True)
        ]
        return sustained_runs(self.timestamps, flags, persistence=persistence)
