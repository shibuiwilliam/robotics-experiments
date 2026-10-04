"""ワーカーの指標記録の単体テスト（M8-14）。

``tests/unit/core/test_metrics.py``（指標の型）と区別するためにファイル名を分けている
——テストディレクトリに ``__init__.py`` が無いので、同名のモジュールは衝突して収集できない。

重点:

1. **未定義の指標名は ``record`` の時点で落ちる** — flush まで遅らせると、どのワーカーの
   どの行が悪いのか分からなくなる。
2. **書き込みの失敗でワーカーを止めない** — 指標は観測であって信念ではない。ただし
   **黙って捨てない**（エラーログ + 落とした件数）。
3. **再送しない** — 古い標本の価値は低く、再送バッファは無限に伸びうる。
4. **溜める上限がある** — 未配線のワーカーでメモリが単調に増えない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from services.metrics import MetricRecorder

from sbp.core.errors import ConfigError
from sbp.core.metrics import MetricSample

if TYPE_CHECKING:
    from collections.abc import Sequence

T0 = datetime(2026, 10, 4, 9, tzinfo=UTC)


class _Sink:
    """書き込みの記録（必要なら落ちる）。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.batches: list[tuple[MetricSample, ...]] = []
        self.fail = fail

    async def __call__(self, samples: Sequence[MetricSample]) -> int:
        if self.fail:
            raise RuntimeError("指標 DB が落ちている")
        self.batches.append(tuple(samples))
        return len(samples)


def _recorder(*, sink: _Sink | None = None, period_s: float = 30.0) -> MetricRecorder:
    return MetricRecorder(
        site="siteA",
        sink=sink,  # type: ignore[arg-type]
        period_s=period_s,
    )


class TestRecording:
    """溜める。"""

    def test_a_sample_is_buffered(self) -> None:
        """記録した標本が溜まる。"""
        recorder = _recorder()
        recorder.record("probe_auroc", 0.9, at=T0, labels={"probe": "Running"})

        assert recorder.buffered == 1

    def test_an_unknown_metric_name_is_refused(self) -> None:
        """列挙に無い指標名は ``record`` で落ちる（flush まで待たない）。"""
        recorder = _recorder()

        with pytest.raises(ConfigError, match="unknown metric"):
            recorder.record("probe_auroq", 0.9, at=T0)

        assert recorder.buffered == 0

    def test_a_ratio_outside_the_range_is_refused(self) -> None:
        """単位 ratio の値域（0〜1）を外れたら落ちる。

        三源の寄与率は対数尤度比の「大きさ」なので、正規化せずに入れるとここで落ちる
        ——落ちることで取り違えに気付ける。
        """
        recorder = _recorder()

        with pytest.raises(ConfigError, match="is a ratio"):
            recorder.record("contrib_observed", 2.31, at=T0)

    def test_a_negative_duration_is_refused(self) -> None:
        """秒・件数は負を許さない。"""
        recorder = _recorder()

        with pytest.raises(ConfigError, match="cannot be negative"):
            recorder.record("lifting_delay", -1.0, at=T0)

    def test_the_buffer_is_capped(self) -> None:
        """上限に当たったら古いものから捨て、捨てた件数を数える。

        未配線のワーカーでも ``record`` は呼ばれるので、上限が無いとメモリが単調に増える。
        """
        recorder = _recorder()
        for index in range(2100):
            recorder.record("probe_auroc", 0.5, at=T0 + timedelta(seconds=index))

        assert recorder.buffered == 2000
        assert recorder.dropped_total == 100


class TestFlushing:
    """書く。"""

    @pytest.mark.asyncio
    async def test_the_first_flush_writes(self) -> None:
        """最初の flush はすぐ書く（周期を待たない）。"""
        sink = _Sink()
        recorder = _recorder(sink=sink)
        recorder.record("probe_auroc", 0.9, at=T0)

        assert await recorder.flush(at=T0) == 1
        assert recorder.written_total == 1
        assert recorder.buffered == 0

    @pytest.mark.asyncio
    async def test_the_period_is_respected(self) -> None:
        """周期が来ていなければ書かない。

        1 イベントごとに INSERT すると、信念更新の回数だけ書き込みが増える。
        """
        sink = _Sink()
        recorder = _recorder(sink=sink, period_s=30.0)
        recorder.record("probe_auroc", 0.9, at=T0)
        await recorder.flush(at=T0)

        recorder.record("probe_auroc", 0.8, at=T0 + timedelta(seconds=5))
        assert await recorder.flush(at=T0 + timedelta(seconds=5)) == 0
        assert recorder.buffered == 1

        assert await recorder.flush(at=T0 + timedelta(seconds=31)) == 1

    @pytest.mark.asyncio
    async def test_force_ignores_the_period(self) -> None:
        """``force`` は周期を無視する（終了時のフラッシュ）。"""
        sink = _Sink()
        recorder = _recorder(sink=sink)
        recorder.record("probe_auroc", 0.9, at=T0)
        await recorder.flush(at=T0)
        recorder.record("probe_auroc", 0.8, at=T0 + timedelta(seconds=1))

        assert await recorder.flush(at=T0 + timedelta(seconds=1), force=True) == 1

    @pytest.mark.asyncio
    async def test_an_empty_buffer_writes_nothing(self) -> None:
        """溜まっていなければ何もしない（空の INSERT を投げない）。"""
        sink = _Sink()
        recorder = _recorder(sink=sink)

        assert await recorder.flush(at=T0, force=True) == 0
        assert sink.batches == []

    @pytest.mark.asyncio
    async def test_a_failing_sink_does_not_raise(self) -> None:
        """書き込みが落ちてもワーカーを止めない（意図的な例外）。

        指標は観測であって信念ではなく、指標 DB の一時的な不調で推定パスを止めるのは
        本末転倒である。
        """
        recorder = _recorder(sink=_Sink(fail=True))
        recorder.record("probe_auroc", 0.9, at=T0)

        assert await recorder.flush(at=T0) == 0
        assert recorder.dropped_total == 1
        assert recorder.written_total == 0

    @pytest.mark.asyncio
    async def test_a_failed_batch_is_not_resent(self) -> None:
        """落ちた batch を再送しない（バッファが無限に伸びない）。"""
        recorder = _recorder(sink=_Sink(fail=True))
        recorder.record("probe_auroc", 0.9, at=T0)
        await recorder.flush(at=T0)

        assert recorder.buffered == 0, "落ちた標本が溜まり続けている"

    @pytest.mark.asyncio
    async def test_without_a_sink_samples_are_counted_as_dropped(self) -> None:
        """未配線でも溜め続けない（捨てた件数は残る）。

        「測っていない」と「書けていない」を区別できるようにしてある。
        """
        recorder = _recorder()
        recorder.record("probe_auroc", 0.9, at=T0)

        assert await recorder.flush(at=T0) == 0
        assert recorder.dropped_total == 1
        assert recorder.buffered == 0
