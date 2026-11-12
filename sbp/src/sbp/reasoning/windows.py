"""照合の入力になる時間窓の蓄積（設計書 §8.2、M7 の運用タスク）。

照合（五類型）は**点ではなく区間**を見る。持続 τ を超えて乖離が続いて初めて不一致とするので
（``DetectionContext.persistence``）、1 イベントごとに判定することができない。そのため
イベントを窓に溜め、周期ごとに ``BeliefSeries`` を組んで検出規則へ渡す。

**窓は必ず有界にする。** 溜め続けると、長時間動かしたワーカーのメモリが単調に増える。実体 ×
フルーエントごとに刻み数の上限を置き、古い刻みから捨てる。捨てた分は再検出できないが、
τ（既定 300 秒）より十分長い窓を持てば検出に影響しない——**上限が τ を下回っていないことを
構築時に検証する**。

**観測列と記録列を別の列として持つ**（``BeliefSeries`` の設計）。片方を欠測として埋めると、
「記録が無かった」と「記録がまだ届いていない」が区別できなくなる。記録はイベントより粗い粒度で
届くので、記録が無い刻みは ``None`` のまま残す。

**刻みを揃えるのは観測側の時刻である。** 記録は不定期に届くので、記録が届いた時刻に最も近い
観測の刻みへ割り当てる。逆（記録の時刻で刻みを作る）にすると、記録が無い期間の観測が
どこにも入らない。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from itertools import pairwise
from typing import TYPE_CHECKING

from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger
from sbp.reasoning.reconciliation.series import BeliefSeries

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

__all__ = ["SeriesWindow", "WindowKey", "WindowStore"]

log = get_logger(__name__)

type WindowKey = tuple[str, str]
"""``(実体 IRI, フルーエント型 IRI)``。``BeliefSeries`` の単位と同じ。"""


@dataclass(slots=True)
class SeriesWindow:
    """1 実体 × 1 フルーエントの時間窓。

    Attributes:
        entity: 実体 IRI。
        fluent: フルーエント型 IRI。
        capacity: 保持する刻み数の上限。
    """

    entity: str
    fluent: str
    capacity: int
    _timestamps: deque[datetime] = field(default_factory=deque)
    _observed: deque[float] = field(default_factory=deque)
    _records: deque[tuple[datetime, float]] = field(default_factory=deque)

    def __post_init__(self) -> None:
        """上限が意味を持つことを検証する。"""
        if self.capacity < 2:
            raise ReasoningError(
                f"a window needs at least 2 samples to have a duration, got {self.capacity}"
            )

    def __len__(self) -> int:
        """保持している刻み数。"""
        return len(self._timestamps)

    @property
    def span(self) -> timedelta:
        """窓が覆っている時間の長さ。"""
        if len(self._timestamps) < 2:
            return timedelta(0)
        return self._timestamps[-1] - self._timestamps[0]

    def observe(self, *, at: datetime, probability: float) -> None:
        """観測由来の刻みを足す。

        時刻が戻ったら無視する（前向きに進む列を保つ。``sbp.reasoning.ingest`` と同じ方針）。

        Args:
            at: 刻みの時刻。
            probability: 観測由来の確率（較正済み）。
        """
        if self._timestamps and at < self._timestamps[-1]:
            log.debug("reasoning.window_out_of_order", entity=self.entity, fluent=self.fluent)
            return
        if self._timestamps and at == self._timestamps[-1]:
            # 同時刻は最新値で置き換える（同じ刻みを 2 本持たない）
            self._observed[-1] = probability
            return
        self._timestamps.append(at)
        self._observed.append(probability)
        self._trim()

    def record(self, *, at: datetime, probability: float) -> bool:
        """記録由来の値を溜める。刻みへの割り当ては列を組むとき（:meth:`to_series`）に行う。

        **到着順に依存させない。** 記録と観測は別のトピックから届き、どちらが先に届くかは
        決まらない。届いた時点の最も近い刻みへ割り当てると、記録が先に届いたとき（刻みが
        まだ無い・遠い刻みしか無い）に割り当てが失われる／ずれる。記録は時刻つきで保持し、
        列を組むときに、その時点の刻みへ割り当てる。

        **刻みは作らない。** 記録だけの刻みを作ると、観測列に埋め草の値が必要になり、
        「観測していない」と「観測して偽だった」が混ざる。

        Args:
            at: 記録の時刻。
            probability: 記録が含意する確率。

        Returns:
            いま観測の刻みがあり、割り当てられる状態なら真。刻みがまだ無ければ偽
            （記録は保持される）。
        """
        self._records.append((at, probability))
        while len(self._records) > self.capacity:
            self._records.popleft()
        return bool(self._timestamps)

    def _trim(self) -> None:
        """上限を超えた古い刻みを捨てる。"""
        while len(self._timestamps) > self.capacity:
            self._timestamps.popleft()
            self._observed.popleft()

    def to_series(self) -> BeliefSeries | None:
        """``BeliefSeries`` にする。

        **刻みが 2 つ未満なら ``None`` を返す。** 1 点では区間が作れず、持続 τ の判定が
        できない——0 と「判定不能」を区別するために ``None`` にする。

        Returns:
            ``BeliefSeries``、または刻みが足りなければ ``None``。
        """
        if len(self._timestamps) < 2:
            return None
        return BeliefSeries(
            entity=self.entity,
            fluent=self.fluent,
            timestamps=tuple(self._timestamps),
            observed=tuple(self._observed),
            recorded=tuple(self._assign_records()),
        )

    def _assign_records(self) -> list[float | None]:
        """記録を最も近い刻みへ割り当てる（同じ刻みに複数なら時刻の新しい記録が勝つ）。"""
        recorded: list[float | None] = [None] * len(self._timestamps)
        # 最も古い刻みより、刻みの間隔の半分を超えて前の記録は割り当てない。刻みが捨てられた
        # 後に残った古い記録が、最古の刻みに貼り付いて現在の観測と比べられるのを防ぐ
        oldest = self._timestamps[0]
        gaps = [b - a for a, b in pairwise(self._timestamps)]
        slack = (sorted(gaps)[len(gaps) // 2] / 2) if gaps else timedelta(0)
        for at, probability in sorted(self._records, key=lambda item: item[0]):
            if at < oldest - slack:
                continue
            nearest = min(
                range(len(self._timestamps)),
                key=lambda i: abs((self._timestamps[i] - at).total_seconds()),
            )
            recorded[nearest] = probability
        return recorded


@dataclass(slots=True)
class WindowStore:
    """実体 × フルーエントごとの窓の集まり。

    Attributes:
        capacity: 1 窓あたりの刻み数の上限。
        min_span: 検出に必要な最小の窓の長さ（持続 τ）。**上限がこれを下回る設定を拒否する**。
        step: 刻みの想定間隔（上限と τ の整合を検査するために使う）。
        windows: 窓の辞書。
    """

    capacity: int = 360
    min_span: timedelta = timedelta(seconds=300)
    step: timedelta = timedelta(seconds=10)
    windows: dict[WindowKey, SeriesWindow] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """窓の上限が持続 τ を覆えることを検証する。

        **ここで落とすのが要点である。** 上限が τ より短いと、乖離が τ 続く前に古い刻みが
        捨てられ、**不一致が永久に検出されない**。件数 0 として静かに通るので、設定ミスが
        「不一致なし」に見える。
        """
        coverable = self.step * (self.capacity - 1)
        if coverable < self.min_span:
            raise ReasoningError(
                f"a window of {self.capacity} samples at {self.step} covers {coverable}, "
                f"shorter than the required persistence {self.min_span}; discrepancies would "
                "never be detected and the count would silently stay 0"
            )

    def window_for(self, *, entity: str, fluent: str) -> SeriesWindow:
        """窓を得る（無ければ作る）。

        Args:
            entity: 実体 IRI。
            fluent: フルーエント型 IRI。
        """
        key = (entity, fluent)
        existing = self.windows.get(key)
        if existing is not None:
            return existing
        created = SeriesWindow(entity=entity, fluent=fluent, capacity=self.capacity)
        self.windows[key] = created
        return created

    def observe(self, *, entity: str, fluent: str, at: datetime, probability: float) -> None:
        """観測を足す。

        Args:
            entity: 実体 IRI。
            fluent: フルーエント型 IRI。
            at: 時刻。
            probability: 較正済み確率。
        """
        self.window_for(entity=entity, fluent=fluent).observe(at=at, probability=probability)

    def record(self, *, entity: str, fluent: str, at: datetime, probability: float) -> bool:
        """記録を足す。

        Args:
            entity: 実体 IRI。
            fluent: フルーエント型 IRI。
            at: 時刻。
            probability: 記録が含意する確率。

        Returns:
            割り当てられたら真。
        """
        return self.window_for(entity=entity, fluent=fluent).record(at=at, probability=probability)

    def ready_series(self) -> list[BeliefSeries]:
        """検出に渡せる列だけを返す。

        **窓が τ を覆っていない列は渡さない。** 短い窓で判定すると、持続の条件を満たさない
        乖離が「まだ続いていない」のか「窓が足りない」のか区別できない。

        Returns:
            検出に渡せる ``BeliefSeries`` の列。
        """
        ready: list[BeliefSeries] = []
        for window in self.windows.values():
            if window.span < self.min_span:
                continue
            series = window.to_series()
            if series is not None:
                ready.append(series)
        return ready

    def prune(self, *, before: datetime) -> int:
        """最後の刻みが ``before`` より古い窓を捨てる。

        Args:
            before: この時刻より古いものを捨てる。

        Returns:
            捨てた窓の数。
        """
        stale = [
            key
            for key, window in self.windows.items()
            if (series := window.to_series()) is not None and series.timestamps[-1] < before
        ]
        for key in stale:
            del self.windows[key]
        if stale:
            log.info("reasoning.windows_pruned", dropped=len(stale), remaining=len(self.windows))
        return len(stale)

    def __len__(self) -> int:
        """保持している窓の数。"""
        return len(self.windows)

    def __iter__(self) -> Iterator[SeriesWindow]:
        """窓を順に返す。"""
        return iter(self.windows.values())
