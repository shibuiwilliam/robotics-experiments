"""観測窓の潜在化と潜在索引への書き込み（M8-24-3、ADR-0023）。

重点:

1. **潜在は観測された時刻にだけ作る** — 窓の頭の ``delays - 1`` 行は潜在を持たない
   （前詰めの偽の潜在を作らない）。
2. **ウォーターマークで冪等** — 書いた時刻以前を再エンコードしない。書いた後に進める。
3. **不正な窓で止まらない** — チャンネルの食い違い・短い窓・読めない行は理由つきで数える。
4. **観測値は永続化しない・スプールを変更しない**（不変条件 9）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pytest

from sbp.core.errors import WorldModelError
from sbp.testing import SynthConfig, SynthWorld
from sbp.worldmodel import KoopmanConfig, KoopmanModel
from sbp.worldmodel.encoding import (
    EncodeRunner,
    JsonlObservationSource,
    ObservationWindow,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from numpy.typing import NDArray

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
DELAYS = 6


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    return SynthWorld(
        config=SynthConfig(start=T0, duration=timedelta(hours=2), cycles=3), seed=2026
    )


@pytest.fixture(scope="module")
def model(world: SynthWorld) -> KoopmanModel:
    untrained = KoopmanModel.untrained(
        model_id="https://sbp.example/wm/koopman",
        version="0.1.0",
        domain="https://sbp.example/ent#Pump",
        channels=world.channels,
        config=KoopmanConfig(delays=DELAYS),
    )
    return untrained.fit(world.observations, seed=2026)


class _Index:
    def __init__(self) -> None:
        self.registered: list[tuple[str, int]] = []
        self.written: list[tuple[str, tuple[datetime, ...], NDArray[np.float64]]] = []
        self.fail = False

    async def register(self, *, wm_version: str, dim: int) -> object:
        self.registered.append((wm_version, dim))
        return None

    async def write(
        self,
        *,
        entity: str,
        wm_version: str,
        level: str,
        timestamps: Sequence[datetime],
        vectors: NDArray[np.float64],
    ) -> int:
        del wm_version, level
        if self.fail:
            raise ConnectionError("db down")
        self.written.append((entity, tuple(timestamps), vectors))
        return len(timestamps)


class _Watermarks:
    def __init__(self, initial: dict[str, datetime] | None = None) -> None:
        self.marks = dict(initial or {})

    async def load(self) -> dict[str, datetime]:
        return dict(self.marks)

    async def save(self, entity: str, processed_through: datetime) -> None:
        self.marks[entity] = max(processed_through, self.marks.get(entity, processed_through))


def _window(world: SynthWorld, start: int, stop: int, entity: str = PUMP) -> ObservationWindow:
    return ObservationWindow(
        entity=entity,
        channels=tuple(world.channels),
        timestamps=tuple(world.timestamps[start:stop]),
        values=world.observations[start:stop],
    )


class _Source:
    def __init__(self, *windows: ObservationWindow, rejected: Sequence[str] = ()) -> None:
        self._windows = windows
        self._rejected = tuple(rejected)

    def windows(self) -> list[ObservationWindow]:
        return list(self._windows)

    @property
    def rejected(self) -> Sequence[str]:
        return self._rejected


class TestEncodeRunner:
    @pytest.mark.asyncio
    async def test_latents_exist_only_for_rows_with_a_full_embedding(
        self, model: KoopmanModel, world: SynthWorld
    ) -> None:
        index = _Index()
        runner = EncodeRunner(model, index=index, watermarks=_Watermarks())
        report = await runner.run(_Source(_window(world, 0, 40)))
        assert report.rows_written == 40 - (DELAYS - 1), "前詰めの偽の潜在を書かない"
        _entity, stamps, vectors = index.written[0]
        assert stamps[0] == world.timestamps[DELAYS - 1]
        assert vectors.shape == (40 - (DELAYS - 1), model.latent_dim)

    @pytest.mark.asyncio
    async def test_the_vectors_match_what_the_model_encodes(
        self, model: KoopmanModel, world: SynthWorld
    ) -> None:
        index = _Index()
        await EncodeRunner(model, index=index, watermarks=_Watermarks()).run(
            _Source(_window(world, 0, 40))
        )
        _entity, stamps, vectors = index.written[0]
        # 末尾の潜在は encode（窓の末尾の潜在）と一致する
        state = model.encode(world.observations[:40], list(world.timestamps[:40]))
        np.testing.assert_allclose(vectors[-1], state.z)
        assert stamps[-1] == state.at

    @pytest.mark.asyncio
    async def test_the_wm_version_is_registered_with_its_dimension(
        self, model: KoopmanModel, world: SynthWorld
    ) -> None:
        index = _Index()
        await EncodeRunner(model, index=index, watermarks=_Watermarks()).run(
            _Source(_window(world, 0, 20))
        )
        assert index.registered == [(model.meta.version_iri, model.latent_dim)]

    @pytest.mark.asyncio
    async def test_a_second_run_over_the_same_window_writes_nothing(
        self, model: KoopmanModel, world: SynthWorld
    ) -> None:
        index = _Index()
        marks = _Watermarks()
        runner = EncodeRunner(model, index=index, watermarks=marks)
        source = _Source(_window(world, 0, 40))
        first = await runner.run(source)
        again = await runner.run(source)
        assert first.rows_written > 0
        assert again.rows_written == 0
        assert again.skipped_processed == 1

    @pytest.mark.asyncio
    async def test_an_overlapping_next_window_writes_only_the_new_rows(
        self, model: KoopmanModel, world: SynthWorld
    ) -> None:
        index = _Index()
        runner = EncodeRunner(model, index=index, watermarks=_Watermarks())
        await runner.run(_Source(_window(world, 0, 40)))
        # 次の窓は直前の delays - 1 行を重ねて置く（エッジの約束）
        second = await runner.run(_Source(_window(world, 40 - (DELAYS - 1), 60)))
        assert second.rows_written == 20
        _entity, stamps, _vectors = index.written[1]
        assert stamps[0] == world.timestamps[40]

    @pytest.mark.asyncio
    async def test_a_failed_write_does_not_advance_the_watermark(
        self, model: KoopmanModel, world: SynthWorld
    ) -> None:
        """書いた後に進める（所見 52）。書けなかった時刻を二度と書かなくならない。"""
        index = _Index()
        index.fail = True
        marks = _Watermarks()
        runner = EncodeRunner(model, index=index, watermarks=marks)
        with pytest.raises(ConnectionError):
            await runner.run(_Source(_window(world, 0, 40)))
        assert marks.marks == {}
        index.fail = False
        again = await runner.run(_Source(_window(world, 0, 40)))
        assert again.rows_written > 0

    @pytest.mark.asyncio
    async def test_bad_windows_are_counted_and_do_not_stop_the_rest(
        self, model: KoopmanModel, world: SynthWorld
    ) -> None:
        index = _Index()
        wrong_channels = ObservationWindow(
            entity=PUMP + "-2",
            channels=("x",),
            timestamps=tuple(world.timestamps[:20]),
            values=np.zeros((20, 1)),
        )
        too_short = _window(world, 0, DELAYS - 1, entity=PUMP + "-3")
        good = _window(world, 0, 30, entity=PUMP)
        report = await EncodeRunner(model, index=index, watermarks=_Watermarks()).run(
            _Source(wrong_channels, too_short, good, rejected=["a.jsonl:3: broken"])
        )
        assert report.windows == 3
        assert report.skipped_mismatch == 1
        assert report.skipped_short == 1
        assert report.rejected == 1
        assert report.rows_written == 30 - (DELAYS - 1)

    @pytest.mark.asyncio
    async def test_entities_have_independent_watermarks(
        self, model: KoopmanModel, world: SynthWorld
    ) -> None:
        index = _Index()
        marks = _Watermarks({PUMP: world.timestamps[100]})
        report = await EncodeRunner(model, index=index, watermarks=marks).run(
            _Source(_window(world, 0, 40), _window(world, 0, 40, entity=PUMP + "-2"))
        )
        assert report.skipped_processed == 1, "P-101 は処理済み"
        assert [entity for entity, _s, _v in index.written] == [PUMP + "-2"]

    @pytest.mark.asyncio
    async def test_health_and_latency_are_reported_for_the_metrics(
        self, model: KoopmanModel, world: SynthWorld
    ) -> None:
        report = await EncodeRunner(model, index=_Index(), watermarks=_Watermarks()).run(
            _Source(_window(world, 0, 60))
        )
        assert report.health is not None
        assert report.health.samples > 0
        assert report.health.wm_version == model.meta.version_iri
        assert report.latency_s and all(value >= 0.0 for value in report.latency_s)

    def test_an_untrained_model_is_refused(self, world: SynthWorld) -> None:
        untrained = KoopmanModel.untrained(
            model_id="m", version="0.1.0", domain="d", channels=world.channels
        )
        with pytest.raises(WorldModelError, match="untrained"):
            EncodeRunner(untrained, index=_Index(), watermarks=_Watermarks())


def _line(
    entity: str = PUMP,
    channels: Sequence[str] = ("a", "b"),
    stamps: Sequence[str] = ("2026-09-12T09:00:00+00:00", "2026-09-12T09:00:10+00:00"),
    values: Sequence[Sequence[float]] = ((1.0, 2.0), (3.0, 4.0)),
) -> str:
    return json.dumps(
        {
            "entity": entity,
            "channels": list(channels),
            "timestamps": list(stamps),
            "values": [list(row) for row in values],
        }
    )


class TestJsonlSource:
    def test_a_valid_line_becomes_a_window(self, tmp_path: Path) -> None:
        path = tmp_path / "w.jsonl"
        path.write_text(_line() + "\n", encoding="utf-8")
        source = JsonlObservationSource(path)
        windows = list(source.windows())
        assert len(windows) == 1
        assert windows[0].entity == PUMP
        assert windows[0].channels == ("a", "b")
        assert windows[0].values.shape == (2, 2)
        assert source.rejected == ()

    def test_a_directory_is_read_in_name_order(self, tmp_path: Path) -> None:
        (tmp_path / "b.jsonl").write_text(_line(entity="B") + "\n", encoding="utf-8")
        (tmp_path / "a.jsonl").write_text(_line(entity="A") + "\n", encoding="utf-8")
        (tmp_path / "ignored.txt").write_text("x", encoding="utf-8")
        entities = [w.entity for w in JsonlObservationSource(tmp_path).windows()]
        assert entities == ["A", "B"]

    @pytest.mark.parametrize(
        "bad",
        [
            "not json",
            _line(values=((1.0, 2.0),)),  # 形が合わない
            _line(values=((1.0, float("nan")), (3.0, 4.0))),  # 非有限
            _line(stamps=("2026-09-12T09:00:10+00:00", "2026-09-12T09:00:00+00:00")),  # 逆行
            _line(stamps=("2026-09-12T09:00:00", "2026-09-12T09:00:10")),  # naive
            _line(entity=""),
            json.dumps({"entity": PUMP}),  # 欠けた項目
        ],
    )
    def test_a_bad_line_is_rejected_with_its_position_and_the_rest_is_read(
        self, tmp_path: Path, bad: str
    ) -> None:
        path = tmp_path / "w.jsonl"
        path.write_text(bad + "\n" + _line(entity="OK") + "\n", encoding="utf-8")
        source = JsonlObservationSource(path)
        assert [w.entity for w in source.windows()] == ["OK"]
        assert len(source.rejected) == 1
        assert source.rejected[0].startswith("w.jsonl:1:")

    def test_the_spool_is_never_modified(self, tmp_path: Path) -> None:
        path = tmp_path / "w.jsonl"
        path.write_text(_line() + "\n", encoding="utf-8")
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        list(JsonlObservationSource(path).windows())
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before
