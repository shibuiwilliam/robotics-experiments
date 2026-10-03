"""スケジューラワーカーの配線のテスト（M7 の運用タスク）。

重点:

1. **事前が無ければ 1 件も発しない** — 推測で埋めない。
2. **人への質問は頻度上限で止まる** — 劣モジュラ性では止まらない（所見 26）。
3. **選ばなかった要求を捨てない** — 次の周期でまた候補になる。
4. **0 件の理由をログに出す** — 要求が無いのか効用が足りないのか上限かを区別する。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from services.scheduler_worker import SchedulerWorker

from sbp.core.config import Settings
from sbp.core.time import Interval
from sbp.reasoning.reconciliation.learning import ObservationRequest
from sbp.reasoning.requests import ObservationMeans
from sbp.reasoning.scheduler import ObservationQuery, QueryKind

T0 = datetime(2026, 6, 10, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
RUNNING = "https://sbp.example/ent#Running"
FAULTED = "https://sbp.example/ent#Faulted"


def _request(*, fluent: str = RUNNING, utility: float = 0.9) -> ObservationRequest:
    return ObservationRequest(
        entity=PUMP,
        fluent=fluent,
        interval=Interval(T0, T0 + timedelta(minutes=10)),
        reason="判断できない",
        utility=utility,
        discrepancy_id=f"d-{fluent.rsplit('#', 1)[-1]}",
    )


class _Sink:
    """発したクエリの記録。"""

    def __init__(self) -> None:
        self.issued: list[ObservationQuery] = []

    def __call__(self, query: ObservationQuery) -> None:
        self.issued.append(query)


def _worker(
    *,
    requests: list[ObservationRequest] | None = None,
    priors: dict[tuple[str, str], float] | None = None,
    means: ObservationMeans | None = None,
) -> tuple[SchedulerWorker, _Sink]:
    sink = _Sink()
    pending = requests if requests is not None else []
    worker = SchedulerWorker(
        Settings(),
        requests=lambda: pending,
        priors=lambda: priors if priors is not None else {},
        sink=sink,
        means=means
        if means is not None
        else ObservationMeans(kind=QueryKind.SAMPLING_RATE, reliability=0.9, cost=0.05),
    )
    return worker, sink


class TestPlanning:
    """計画と発行。"""

    @pytest.mark.asyncio
    async def test_a_request_with_a_prior_is_issued(self) -> None:
        """事前がある要求はクエリとして発される。"""
        worker, sink = _worker(requests=[_request()], priors={(PUMP, RUNNING): 0.5})
        assert await worker.step() == 1
        assert len(sink.issued) == 1
        assert worker.issued_total == 1

    @pytest.mark.asyncio
    async def test_without_priors_nothing_is_issued(self) -> None:
        """事前が無ければ 1 件も発しない（推測で埋めない）。"""
        worker, sink = _worker(requests=[_request()], priors={})
        assert await worker.step() == 0
        assert sink.issued == []
        assert worker.skipped_total == 1

    @pytest.mark.asyncio
    async def test_no_requests_means_no_queries(self) -> None:
        """要求が無ければ何もしない。"""
        worker, sink = _worker(requests=[], priors={(PUMP, RUNNING): 0.5})
        assert await worker.step() == 0
        assert sink.issued == []

    @pytest.mark.asyncio
    async def test_a_worthless_observation_is_not_issued(self) -> None:
        """効用が 0 以下の観測は発しない。

        事前が 0 に近く（既にほぼ確信している）価値も低ければ、情報利得がコストを下回る。
        **予算を増やしても無価値な観測は増えない**（`schedule` の設計）。
        """
        worker, sink = _worker(
            requests=[_request(utility=0.0)],
            priors={(PUMP, RUNNING): 0.999},
            means=ObservationMeans(kind=QueryKind.SAMPLING_RATE, reliability=0.9, cost=0.5),
        )
        assert await worker.step() == 0
        assert sink.issued == []


class TestHumanQueryLimit:
    """人への質問の頻度上限。"""

    @pytest.mark.asyncio
    async def test_human_queries_are_capped(self) -> None:
        """上限を超える人への質問は選ばれない。

        **劣モジュラ性では止まらない**（所見 26）——安い観測をもう 1 回するのは実際に少し
        情報を増やすので効用は正のまま残る。止めるのは頻度上限の役目である。
        """
        limit = Settings().reasoning.max_human_queries_per_hour
        many = [_request(fluent=f"https://sbp.example/ent#F{index}") for index in range(limit + 3)]
        priors = {(PUMP, request.fluent): 0.5 for request in many}
        worker, sink = _worker(
            requests=many,
            priors=priors,
            means=ObservationMeans(kind=QueryKind.HUMAN_QUESTION, reliability=0.99, cost=0.01),
        )
        await worker.step()
        assert len(sink.issued) <= limit

    @pytest.mark.asyncio
    async def test_asking_a_human_is_recorded(self) -> None:
        """人へ聞いたら記録する（次の周期の上限判定に効く）。"""
        worker, _sink = _worker(
            requests=[_request()],
            priors={(PUMP, RUNNING): 0.5},
            means=ObservationMeans(kind=QueryKind.HUMAN_QUESTION, reliability=0.99, cost=0.01),
        )
        await worker.step()
        assert worker.human_log.count_within(window=timedelta(hours=1), at=T0) >= 0
        assert len(worker.human_log.asked_at) == 1

    @pytest.mark.asyncio
    async def test_sensor_queries_are_not_recorded_as_human(self) -> None:
        """センサ系の観測は人への質問に数えない。"""
        worker, _sink = _worker(requests=[_request()], priors={(PUMP, RUNNING): 0.5})
        await worker.step()
        assert worker.human_log.asked_at == []


class TestPeriod:
    """周期。"""

    def test_planning_is_due_on_the_first_loop(self) -> None:
        """初回は回す。"""
        worker, _sink = _worker()
        assert worker.plan_due(at=T0)

    def test_planning_is_not_due_again_immediately(self) -> None:
        """周期内は回さない。"""
        worker, _sink = _worker()
        worker.mark_plan_done(at=T0)
        assert not worker.plan_due(at=T0 + timedelta(seconds=1))

    @pytest.mark.asyncio
    async def test_requests_survive_a_cycle_that_issued_nothing(self) -> None:
        """選ばなかった要求は消えない。

        捨てると、一度コストが高いと判定された観測が永久に実行されない（信念が変わって
        事前が動けば効用は変わる）。
        """
        pending = [_request()]
        worker = SchedulerWorker(
            Settings(),
            requests=lambda: pending,
            priors=lambda: {},  # 事前が無いので変換できない
            sink=_Sink(),
        )
        await worker.step()
        assert pending == [_request()]


class TestUnconfiguredPaths:
    """設定が無いときに黙って成功しない。"""

    @pytest.mark.asyncio
    async def test_without_a_request_source_nothing_happens(self) -> None:
        """要求の供給元が無ければ何もしない（起動時に警告する）。"""
        worker = SchedulerWorker(Settings())
        assert await worker.step() == 0

    @pytest.mark.asyncio
    async def test_without_a_prior_source_nothing_happens(self) -> None:
        """事前の供給元が無ければ何もしない。"""
        worker = SchedulerWorker(Settings(), requests=lambda: [_request()])
        assert await worker.step() == 0

    @pytest.mark.asyncio
    async def test_without_a_sink_planning_still_runs(self) -> None:
        """sink が無くても計画は進む（dry 相当）。"""
        worker = SchedulerWorker(
            Settings(),
            requests=lambda: [_request()],
            priors=lambda: {(PUMP, RUNNING): 0.5},
        )
        assert await worker.step() == 1
        assert worker.issued_total == 1

    def test_the_budget_comes_from_settings(self) -> None:
        """予算は Settings が決める（定数をワーカーに置かない）。"""
        worker, _sink = _worker()
        settings = worker.settings.reasoning
        assert worker.budget.max_queries == settings.scheduler_max_queries
        assert worker.budget.total_cost == settings.scheduler_total_cost
        assert worker.budget.max_human_queries == settings.max_human_queries_per_hour
