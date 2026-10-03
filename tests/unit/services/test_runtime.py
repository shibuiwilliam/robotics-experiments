"""ワーカーランタイムの単体テスト（インフラ・テストルール、M5-08）。

重点:

1. **必須の依存が落ちていたら起動しない** — 「処理件数 0 で正常稼働」に見える状態を作らない。
2. **停止要求でループを中断せず終える** — 処理中のイベントを捨てるとオフセットだけ進む。
3. **終了時にコミットする** — 飛ばすと毎回の再起動で再処理が起きる。
4. **結合は周期で回す** — 1 イベントごとに回すと処理が追いつかない。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from services.belief_worker import WORKER_NAME, BeliefWorker
from services.runtime import ReadinessCheck, WorkerRuntime, check_readiness

from sbp.core.config import Settings
from sbp.core.errors import DependencyUnavailableError
from sbp.core.time import now


class TestReadiness:
    def test_a_missing_required_dependency_blocks_startup(self) -> None:
        with pytest.raises(DependencyUnavailableError, match="unreachable"):
            check_readiness((ReadinessCheck(name="redpanda", probe=lambda: False),), worker="w")

    def test_a_probe_that_raises_counts_as_unreachable(self) -> None:
        def explode() -> bool:
            raise OSError("connection refused")

        with pytest.raises(DependencyUnavailableError, match="redpanda"):
            check_readiness((ReadinessCheck(name="redpanda", probe=explode),), worker="w")

    def test_an_optional_dependency_does_not_block(self) -> None:
        check_readiness(
            (ReadinessCheck(name="grafana", probe=lambda: False, required=False),), worker="w"
        )

    def test_all_reachable_passes(self) -> None:
        check_readiness(
            (
                ReadinessCheck(name="redpanda", probe=lambda: True),
                ReadinessCheck(name="oxigraph", probe=lambda: True),
            ),
            worker="w",
        )

    def test_every_failing_dependency_is_named(self) -> None:
        """1 つだけ挙げると、直した後にもう 1 つで落ちる（原因を一度に見せる）。"""
        with pytest.raises(DependencyUnavailableError) as excinfo:
            check_readiness(
                (
                    ReadinessCheck(name="redpanda", probe=lambda: False),
                    ReadinessCheck(name="oxigraph", probe=lambda: False),
                ),
                worker="w",
            )
        assert "redpanda" in str(excinfo.value)
        assert "oxigraph" in str(excinfo.value)


class TestRunLoop:
    @pytest.mark.asyncio
    async def test_it_stops_after_the_current_loop(self) -> None:
        runtime = WorkerRuntime(name="t", settings=Settings())
        finished: list[int] = []

        async def step() -> int:
            finished.append(len(finished))
            if len(finished) == 3:
                runtime.request_stop(reason="test")
            return 1

        total = await runtime.run(step, idle_sleep_s=0.01)
        assert total == 3
        assert finished == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_it_commits_on_shutdown(self) -> None:
        """コミットを飛ばすと at-least-once の再処理が毎回の再起動で起きる。"""
        runtime = WorkerRuntime(name="t", settings=Settings())
        committed: list[int] = []

        async def step() -> int:
            runtime.request_stop(reason="test")
            return 1

        async def commit() -> None:
            committed.append(1)

        await runtime.run(step, commit=commit, idle_sleep_s=0.01)
        assert committed == [1]

    @pytest.mark.asyncio
    async def test_it_commits_even_when_the_step_raises(self) -> None:
        """例外で落ちても処理済みの分はコミットする。"""
        runtime = WorkerRuntime(name="t", settings=Settings())
        committed: list[int] = []

        async def step() -> int:
            raise RuntimeError("boom")

        async def commit() -> None:
            committed.append(1)

        with pytest.raises(RuntimeError, match="boom"):
            await runtime.run(step, commit=commit)
        assert committed == [1]

    @pytest.mark.asyncio
    async def test_an_idle_loop_does_not_spin(self) -> None:
        """何も来ていないときに忙しくループを回して CPU を焼かない。"""
        runtime = WorkerRuntime(name="t", settings=Settings())
        calls: list[int] = []

        async def step() -> int:
            calls.append(1)
            return 0

        started = asyncio.get_running_loop().time()
        await runtime.run(step, idle_sleep_s=0.05, max_iterations=3)
        elapsed = asyncio.get_running_loop().time() - started
        assert len(calls) == 3
        assert elapsed >= 0.1  # 3 回のうち少なくとも 2 回は待っている

    @pytest.mark.asyncio
    async def test_a_stop_request_interrupts_the_idle_wait(self) -> None:
        """停止が待ち時間のぶん遅れないこと。"""
        runtime = WorkerRuntime(name="t", settings=Settings())

        async def step() -> int:
            runtime.request_stop(reason="test")
            return 0

        started = asyncio.get_running_loop().time()
        await runtime.run(step, idle_sleep_s=5.0)
        assert asyncio.get_running_loop().time() - started < 1.0

    @pytest.mark.asyncio
    async def test_readiness_failure_prevents_any_step(self) -> None:
        runtime = WorkerRuntime(
            name="t",
            settings=Settings(),
            checks=(ReadinessCheck(name="redpanda", probe=lambda: False),),
        )
        calls: list[int] = []

        async def step() -> int:
            calls.append(1)
            return 1

        with pytest.raises(DependencyUnavailableError):
            await runtime.run(step)
        assert calls == []


class TestBeliefWorkerCoupling:
    def test_the_first_cycle_is_due(self) -> None:
        worker = BeliefWorker(settings=Settings())
        assert worker.coupling_due(at=now())

    def test_it_is_not_due_again_immediately(self) -> None:
        """1 イベントごとに結合を回すと、辺数に比例した負荷で処理が追いつかない。"""
        worker = BeliefWorker(settings=Settings())
        moment = now()
        worker.mark_coupling_done(at=moment)
        assert not worker.coupling_due(at=moment)
        assert not worker.coupling_due(at=moment + timedelta(seconds=1))

    def test_it_is_due_after_the_period(self) -> None:
        worker = BeliefWorker(settings=Settings())
        moment = now()
        worker.mark_coupling_done(at=moment)
        assert worker.coupling_due(at=moment + worker.coupling_period)

    def test_the_period_comes_from_settings(self) -> None:
        settings = Settings()
        worker = BeliefWorker(settings=settings)
        assert worker.coupling_period == timedelta(seconds=settings.reasoning.bp_period_s)

    def test_it_declares_its_dependencies(self) -> None:
        worker = BeliefWorker(settings=Settings())
        names = {check.name for check in worker.runtime.checks}
        assert names == {"redpanda", "oxigraph"}
        assert worker.runtime.name == WORKER_NAME
