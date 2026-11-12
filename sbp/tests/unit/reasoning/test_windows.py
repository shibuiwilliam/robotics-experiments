"""照合の時間窓のテスト（M7 の運用タスク）。

重点:

1. **窓が持続 τ を覆えない設定を構築時に拒否する** — 覆えないと不一致が永久に検出されず、
   件数 0 として静かに通る。
2. **窓は有界** — 溜め続けるとメモリが単調に増える。
3. **記録が無い刻みは ``None`` のまま** — 「記録が無かった」と「まだ届いていない」を混ぜない。
4. **刻みを揃えるのは観測側** — 記録だけの刻みを作らない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sbp.core.errors import ReasoningError
from sbp.reasoning.windows import SeriesWindow, WindowStore

T0 = datetime(2026, 6, 10, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
RUNNING = "https://sbp.example/ent#Running"
STEP = timedelta(seconds=10)


def _window(capacity: int = 10) -> SeriesWindow:
    return SeriesWindow(entity=PUMP, fluent=RUNNING, capacity=capacity)


def _store(**kwargs: object) -> WindowStore:
    defaults: dict[str, object] = {
        "capacity": 40,
        "min_span": timedelta(seconds=120),
        "step": STEP,
    }
    defaults.update(kwargs)
    return WindowStore(**defaults)  # type: ignore[arg-type]


class TestWindowCapacityGuard:
    """上限と持続 τ の整合。"""

    def test_a_window_too_short_for_the_persistence_is_refused(self) -> None:
        """τ を覆えない設定は構築時に落とす。

        **件数 0 として静かに通るのが最悪である。** 乖離が τ 続く前に古い刻みが捨てられ、
        不一致が永久に検出されないのに「不一致なし」に見える。
        """
        with pytest.raises(ReasoningError, match="never be detected"):
            _store(capacity=5, min_span=timedelta(seconds=300), step=STEP)

    def test_a_window_that_covers_the_persistence_is_accepted(self) -> None:
        """覆えるなら通る（上限ちょうども可）。

        ``assert store`` と書かないこと——``WindowStore`` は ``__len__`` を持つので、
        窓が 0 個の状態は偽になる（コンテナとしては正しい挙動）。
        """
        # 31 刻み × 10 秒 = 300 秒
        store = _store(capacity=31, min_span=timedelta(seconds=300), step=STEP)
        assert store.capacity == 31

    def test_a_single_sample_window_is_refused(self) -> None:
        """1 刻みの窓は区間を持てない。"""
        with pytest.raises(ReasoningError, match="at least 2 samples"):
            SeriesWindow(entity=PUMP, fluent=RUNNING, capacity=1)


class TestObserve:
    """観測の蓄積。"""

    def test_samples_accumulate_in_order(self) -> None:
        """刻みが順に溜まる。"""
        window = _window()
        for index in range(5):
            window.observe(at=T0 + STEP * index, probability=0.9)
        assert len(window) == 5
        assert window.span == STEP * 4

    def test_the_window_is_bounded(self) -> None:
        """上限を超えたら古い刻みを捨てる（メモリが単調に増えない）。"""
        window = _window(capacity=3)
        for index in range(10):
            window.observe(at=T0 + STEP * index, probability=0.9)
        assert len(window) == 3
        series = window.to_series()
        assert series is not None
        assert series.timestamps[0] == T0 + STEP * 7

    def test_out_of_order_samples_are_ignored(self) -> None:
        """時刻が戻った刻みは入れない（前向きに進む列を保つ）。"""
        window = _window()
        window.observe(at=T0 + STEP * 5, probability=0.9)
        window.observe(at=T0, probability=0.1)
        assert len(window) == 1

    def test_the_same_timestamp_replaces_the_value(self) -> None:
        """同時刻は最新値で置き換える（同じ刻みを 2 本持たない）。"""
        window = _window()
        window.observe(at=T0, probability=0.1)
        window.observe(at=T0, probability=0.9)
        assert len(window) == 1
        # 刻みを 1 つ足して列にし、置き換わった値を確かめる
        window.observe(at=T0 + STEP, probability=0.5)
        series = window.to_series()
        assert series is not None
        assert series.observed == (0.9, 0.5)


class TestRecord:
    """記録の割り当て。"""

    def test_a_record_lands_on_the_nearest_observed_sample(self) -> None:
        """記録は最も近い観測の刻みへ入る（記録は不定期に届く）。"""
        window = _window()
        for index in range(5):
            window.observe(at=T0 + STEP * index, probability=0.9)
        assert window.record(at=T0 + timedelta(seconds=22), probability=0.1)
        series = window.to_series()
        assert series is not None
        # 22 秒は 20 秒（index 2）に最も近い
        assert series.recorded[2] == 0.1

    def test_a_record_without_observations_is_dropped(self) -> None:
        """観測の刻みが無ければ記録は入らない。

        **記録だけの刻みを作らない。** 作ると観測列に埋め草の値が必要になり、
        「観測していない」と「観測して偽だった」が混ざる。
        """
        window = _window()
        assert not window.record(at=T0, probability=0.1)
        assert len(window) == 0

    def test_a_record_that_arrives_before_the_ticks_is_not_lost(self) -> None:
        """記録が観測より先に届いても、刻みが揃ったときに割り当てられる（G8 で発見）。

        記録と観測は別のトピックから届き、どちらが先かは決まらない。届いた時点の刻みへ
        割り当てると、先に届いた記録が失われる。
        """
        early = _window()
        early.record(at=T0 + timedelta(seconds=22), probability=0.1)
        late = _window()
        for index in range(5):
            early.observe(at=T0 + STEP * index, probability=0.9)
            late.observe(at=T0 + STEP * index, probability=0.9)
        late.record(at=T0 + timedelta(seconds=22), probability=0.1)
        a, b = early.to_series(), late.to_series()
        assert a is not None and b is not None
        assert a.recorded == b.recorded
        assert a.recorded[2] == 0.1

    def test_a_stale_record_older_than_the_oldest_tick_is_not_pinned_to_it(self) -> None:
        """刻みが捨てられた後に残った古い記録が、最古の刻みに貼り付かない（レビュー指摘）。"""
        window = _window()
        window.record(at=T0 - timedelta(hours=3), probability=0.1)
        for index in range(5):
            window.observe(at=T0 + STEP * index, probability=0.9)
        series = window.to_series()
        assert series is not None
        assert series.recorded[0] is None

    def test_the_newer_record_wins_on_the_same_tick(self) -> None:
        window = _window()
        for index in range(5):
            window.observe(at=T0 + STEP * index, probability=0.9)
        window.record(at=T0 + timedelta(seconds=21), probability=0.8)
        window.record(at=T0 + timedelta(seconds=19), probability=0.2)  # 古い時刻だが後から届く
        series = window.to_series()
        assert series is not None
        assert series.recorded[2] == 0.8

    def test_samples_without_records_stay_none(self) -> None:
        """記録が無い刻みは ``None`` のまま。

        埋めると「記録が無かった」と「まだ届いていない」が区別できなくなる。
        """
        window = _window()
        for index in range(3):
            window.observe(at=T0 + STEP * index, probability=0.9)
        window.record(at=T0, probability=0.2)
        series = window.to_series()
        assert series is not None
        assert series.recorded == (0.2, None, None)


class TestToSeries:
    """``BeliefSeries`` への変換。"""

    def test_a_single_sample_yields_none(self) -> None:
        """1 刻みでは区間が作れないので ``None``。

        0 ではなく ``None`` にするのは、「判定不能」と「乖離なし」を区別するため。
        """
        window = _window()
        window.observe(at=T0, probability=0.9)
        assert window.to_series() is None

    def test_an_empty_window_yields_none(self) -> None:
        """空の窓も ``None``。"""
        assert _window().to_series() is None

    def test_two_samples_yield_a_series(self) -> None:
        """2 刻みあれば列になる。"""
        window = _window()
        window.observe(at=T0, probability=0.9)
        window.observe(at=T0 + STEP, probability=0.8)
        series = window.to_series()
        assert series is not None
        assert series.entity == PUMP
        assert series.fluent == RUNNING
        assert len(series.timestamps) == 2


class TestWindowStore:
    """窓の集まり。"""

    def test_windows_are_per_entity_and_fluent(self) -> None:
        """実体 × フルーエントごとに分かれる。"""
        store = _store()
        store.observe(entity=PUMP, fluent=RUNNING, at=T0, probability=0.9)
        store.observe(entity=PUMP, fluent="other", at=T0, probability=0.9)
        store.observe(entity="other-entity", fluent=RUNNING, at=T0, probability=0.9)
        assert len(store) == 3

    def test_only_series_covering_the_persistence_are_ready(self) -> None:
        """窓が τ を覆っていない列は検出に渡さない。

        短い窓で判定すると、持続の条件を満たさない乖離が「まだ続いていない」のか
        「窓が足りない」のか区別できない。
        """
        store = _store(min_span=timedelta(seconds=120))
        # 60 秒ぶんしか溜めない
        for index in range(7):
            store.observe(entity=PUMP, fluent=RUNNING, at=T0 + STEP * index, probability=0.9)
        assert store.ready_series() == []

        for index in range(7, 14):
            store.observe(entity=PUMP, fluent=RUNNING, at=T0 + STEP * index, probability=0.9)
        assert len(store.ready_series()) == 1

    def test_pruning_drops_stale_windows(self) -> None:
        """古い窓を捨てられる。"""
        store = _store()
        store.observe(entity=PUMP, fluent=RUNNING, at=T0, probability=0.9)
        store.observe(entity=PUMP, fluent=RUNNING, at=T0 + STEP, probability=0.9)
        store.observe(entity="fresh", fluent=RUNNING, at=T0 + timedelta(hours=2), probability=0.9)
        store.observe(
            entity="fresh", fluent=RUNNING, at=T0 + timedelta(hours=2) + STEP, probability=0.9
        )
        assert store.prune(before=T0 + timedelta(hours=1)) == 1
        assert len(store) == 1

    def test_pruning_nothing_returns_zero(self) -> None:
        """捨てるものが無ければ 0。"""
        assert _store().prune(before=T0) == 0

    def test_iteration_yields_windows(self) -> None:
        """窓を順に辿れる（運用の観測に使う）。"""
        store = _store()
        store.observe(entity=PUMP, fluent=RUNNING, at=T0, probability=0.9)
        assert [window.entity for window in store] == [PUMP]
