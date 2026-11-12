"""ワーカーが測った値を ``metrics`` 表に書く（M8-14）。

M8-06 で表・エクスポート・ダッシュボードは通ったが、**``MetricStore.write`` を呼ぶ運用コードが
1 つも無かった**（``docs/PROGRESS.md`` 所見 71）。ここがその呼び出し側である。

**溜めて周期ごとに書く。** 1 イベントごとに INSERT すると、信念更新の回数だけ書き込みが増える
（イベント 1 件あたり 4〜5 系列）。ダッシュボードのスクレイプ間隔は 30 秒なので、それより
細かく書いても見える値は変わらない。

**未定義の指標名は ``record`` の時点で落とす。** :func:`sbp.core.metrics.metric` を通すので、
綴り間違いは flush まで待たずにワーカーの中で落ちる——flush で落とすと、どのワーカーの
どの行が悪いのかが分からなくなる。

**書き込みの失敗でワーカーを止めない。** これは「例外を握りつぶさない」規約の意図的な例外で
ある。指標は観測であって信念ではなく、指標 DB の一時的な不調で推定パスを止めるのは本末転倒で
ある。ただし**黙って捨てない**——エラーログを出し、落とした件数を ``dropped`` に数える。
再送はしない（古い標本の価値は低く、再送バッファは無限に伸びうる。リトライは eventlog の
consumer 層と ``llm.Provider`` の 2 箇所だけ、という規約も保つ）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sbp.core.logging import get_logger
from sbp.core.metrics import MetricSample, metric

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import datetime

    MetricWriter = Callable[[Sequence[MetricSample]], Awaitable[int]]
    """``標本の列 -> 書いた件数``（``MetricStore.write`` を包む）。"""

__all__ = ["MetricRecorder"]

log = get_logger(__name__)

_DEFAULT_PERIOD_S = 30.0
"""書き込み周期（秒）。ダッシュボードのスクレイプ間隔に合わせた既定。"""

_BUFFER_CAP = 2000
"""溜める標本の上限。

``sink`` が無い（未配線の）ワーカーでも ``record`` は呼ばれるので、上限が無いとメモリが
単調に増える。上限に当たったら**古いものから捨てる**——新しい値のほうが運用判断に使える。
"""


class MetricRecorder:
    """測った値を溜めて周期ごとに書く。

    Attributes:
        site: 拠点識別子。
        period: 書き込み周期。
        written_total: 書けた件数の累計。
        dropped_total: 書けずに捨てた件数の累計。
    """

    def __init__(
        self,
        *,
        site: str,
        sink: MetricWriter | None = None,
        period_s: float = _DEFAULT_PERIOD_S,
    ) -> None:
        """Args:
        site: 拠点識別子。
        sink: 書き込み（``None`` なら測るだけで書かない。単体テストと未配線のワーカー）。
        period_s: 書き込み周期（秒）。
        """
        self.site = site
        self.period = timedelta(seconds=period_s)
        self._sink = sink
        self._buffer: list[MetricSample] = []
        self._last_flush: datetime | None = None
        self.written_total = 0
        self.dropped_total = 0
        if sink is None:
            log.info(
                "metrics.not_wired",
                site=site,
                detail="sink が無いので測るだけで書かない（ダッシュボードには出ない）",
            )

    @property
    def buffered(self) -> int:
        """まだ書いていない標本の数。"""
        return len(self._buffer)

    def record(
        self,
        key: str,
        value: float,
        *,
        at: datetime,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """標本を 1 件溜める。

        Args:
            key: 指標の系列名（``sbp.core.metrics.METRICS`` にあるもの）。
            value: 値。単位の値域は :class:`~sbp.core.metrics.MetricSample` が検証する。
            at: 測定時刻。
            labels: 絞り込みの軸（``entity`` / ``kind`` など）。

        Raises:
            ConfigError: 未定義の指標名。**ここで落とす**——flush まで遅らせると、どの
                ワーカーのどの行が悪いのか分からなくなる。
            ConfigError: 単位に対して値域が外れている場合（``MetricSample`` の検証）。
        """
        sample = MetricSample(name=metric(key), value=value, at=at, labels=dict(labels or {}))
        if len(self._buffer) >= _BUFFER_CAP:
            # 古いものから捨てる。**捨てたことを数える**——黙って減ると「測っていない」と
            # 「書けていない」が区別できない
            self._buffer.pop(0)
            self.dropped_total += 1
        self._buffer.append(sample)

    def flush_due(self, *, at: datetime) -> bool:
        """この時点で書くべきか。

        Args:
            at: 判定時刻。
        """
        if not self._buffer:
            return False
        if self._last_flush is None:
            return True
        return at - self._last_flush >= self.period

    async def flush(self, *, at: datetime, force: bool = False) -> int:
        """溜めた標本を書く（周期が来ていなければ何もしない）。

        Args:
            at: 判定・記録の時刻。
            force: 周期を無視して書く（終了時のフラッシュ）。

        Returns:
            書いた件数（周期前・標本なし・未配線なら 0）。
        """
        if not force and not self.flush_due(at=at):
            return 0
        if not self._buffer:
            return 0
        batch = tuple(self._buffer)
        self._buffer.clear()
        self._last_flush = at
        if self._sink is None:
            # 未配線。溜め続けないために捨てるが、数は残す
            self.dropped_total += len(batch)
            return 0
        try:
            written = await self._sink(batch)
        except Exception as exc:
            self.dropped_total += len(batch)
            log.error(
                "metrics.write_failed",
                site=self.site,
                dropped=len(batch),
                dropped_total=self.dropped_total,
                error=str(exc),
                detail="指標は観測であって信念ではないので止めない。再送もしない",
            )
            return 0
        self.written_total += written
        log.debug("metrics.flushed", site=self.site, written=written)
        return written
