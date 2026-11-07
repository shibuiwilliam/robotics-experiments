"""解決の適用の指標（M8-15）。

``resolution_time`` / ``reliability_updates`` / ``learning_yield``。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from services.learning_worker import LearningWorker
from services.metrics import MetricRecorder
from tests.unit.services.test_learning_worker import (  # pyright: ignore[reportMissingImports]
    _Observations,  # pyright: ignore[reportPrivateUsage]
    _Queue,  # pyright: ignore[reportPrivateUsage]
    _resolution_row,  # pyright: ignore[reportPrivateUsage]
)

from sbp.core.config import Settings
from sbp.core.metrics import MetricSample

if TYPE_CHECKING:
    from collections.abc import Sequence


class _Sink:
    def __init__(self) -> None:
        self.samples: list[MetricSample] = []

    async def __call__(self, samples: Sequence[MetricSample]) -> int:
        self.samples.extend(samples)
        return len(samples)


def _worker(sink: _Sink, *outcomes: str) -> LearningWorker:
    return LearningWorker(
        Settings(),
        decisions=_Queue(
            [_resolution_row(decision_id=f"d{i}", outcome=o) for i, o in enumerate(outcomes)]
        ),  # type: ignore[arg-type]
        observations=_Observations(),
        metrics=MetricRecorder(site="siteA", sink=sink, period_s=0.0),
        period_s=0.0,
    )


@pytest.mark.asyncio
async def test_a_resolution_writes_its_three_series() -> None:
    sink = _Sink()
    worker = _worker(sink, "undetermined")
    await worker.step()
    names = {s.name.key for s in sink.samples}
    assert {"resolution_time", "reliability_updates", "learning_yield"} <= names


@pytest.mark.asyncio
async def test_learning_yield_counts_resolutions_that_taught_nothing() -> None:
    """観測要求を積む「判定不能」は学習信号。何も出ない解決は分母にだけ入る。"""
    sink = _Sink()
    worker = _worker(sink, "undetermined", "both_wrong")
    await worker.step()
    assert worker.resolved_total == 2
    assert 0.0 < worker.productive_total / worker.resolved_total <= 1.0
    last = [s for s in sink.samples if s.name.key == "learning_yield"][-1]
    assert last.value == pytest.approx(worker.productive_total / worker.resolved_total)


@pytest.mark.asyncio
async def test_without_a_recorder_nothing_is_measured_but_counts_still_move() -> None:
    worker = LearningWorker(
        Settings(),
        decisions=_Queue([_resolution_row(outcome="undetermined")]),  # type: ignore[arg-type]
        observations=_Observations(),
        period_s=0.0,
    )
    await worker.step()
    assert worker.resolved_total == 1


@pytest.mark.asyncio
async def test_resolutions_of_one_cycle_do_not_share_a_series_key() -> None:
    """同じ周期の同じ類型の標本が同じ (名前, 時刻, ラベル) だと、表の上書きで失われる。"""
    sink = _Sink()
    await _worker(sink, "undetermined", "undetermined", "undetermined").step()
    keys = [
        (s.name.key, s.at, tuple(sorted(s.labels.items())))
        for s in sink.samples
        if s.name.key == "resolution_time"
    ]
    assert len(keys) == 3
    assert len(set(keys)) == 3
