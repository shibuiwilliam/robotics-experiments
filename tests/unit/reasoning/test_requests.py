"""``ObservationRequest`` → ``ObservationQuery`` の変換のテスト（M7 の運用タスク）。

重点:

1. **事前確率を推測で埋めない** — 無ければ変換しない。埋めると EIG が意味を失う。
2. **変換できなかった分を別に返す** — 落とした件数が見えないと、要求が消えたことが分からない。
3. **深刻度をそのまま価値にする** — 照合とスケジューラで価値観を二重に持たない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sbp.core.time import Interval
from sbp.reasoning.reconciliation.learning import ObservationRequest
from sbp.reasoning.requests import ObservationMeans, to_queries, to_query
from sbp.reasoning.scheduler import QueryKind

T0 = datetime(2026, 6, 10, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
RUNNING = "https://sbp.example/ent#Running"
FAULTED = "https://sbp.example/ent#Faulted"

MEANS = ObservationMeans(kind=QueryKind.SAMPLING_RATE, reliability=0.9, cost=0.05)


def _request(*, fluent: str = RUNNING, utility: float = 0.7) -> ObservationRequest:
    return ObservationRequest(
        entity=PUMP,
        fluent=fluent,
        interval=Interval(T0, T0 + timedelta(minutes=10)),
        reason="観測と記録のどちらが正しいか判断できない",
        utility=utility,
        discrepancy_id="d-1",
    )


class TestToQuery:
    """1 件の変換。"""

    def test_a_request_with_a_prior_converts(self) -> None:
        """事前があれば変換できる。"""
        query = to_query(_request(), priors={(PUMP, RUNNING): 0.5}, means=MEANS)
        assert query is not None
        assert query.entity == PUMP
        assert query.fluents == (RUNNING,)
        assert query.prior[RUNNING] == 0.5

    def test_a_request_without_a_prior_is_not_converted(self) -> None:
        """事前が無ければ変換しない。**0.5 を埋めない。**

        埋めると、あらゆる要求が同じ中間の事前を持つことになり、スケジューラの選別が
        意味を失う。「情報が無い」のであって「価値が無い」のではない。
        """
        assert to_query(_request(), priors={}, means=MEANS) is None

    def test_the_prior_of_another_fluent_does_not_count(self) -> None:
        """別のフルーエントの事前では変換しない（鍵は実体 × フルーエント）。"""
        assert to_query(_request(), priors={(PUMP, FAULTED): 0.5}, means=MEANS) is None

    def test_the_severity_becomes_the_value(self) -> None:
        """深刻度がそのまま ``v(x)`` になる。

        ここで再計算すると、照合とスケジューラで別の価値観が並ぶ。
        """
        query = to_query(_request(utility=0.42), priors={(PUMP, RUNNING): 0.5}, means=MEANS)
        assert query is not None
        assert query.value[RUNNING] == 0.42

    def test_the_means_decide_reliability_and_cost(self) -> None:
        """信頼度とコストは観測手段から来る（要求は持っていない）。"""
        query = to_query(_request(), priors={(PUMP, RUNNING): 0.5}, means=MEANS)
        assert query is not None
        assert query.reliability == 0.9
        assert query.cost == 0.05
        assert query.kind is QueryKind.SAMPLING_RATE

    def test_the_reason_keeps_the_discrepancy_id(self) -> None:
        """由来の不一致が辿れる（なぜこの観測を考えたかが残る）。"""
        query = to_query(_request(), priors={(PUMP, RUNNING): 0.5}, means=MEANS)
        assert query is not None
        assert "d-1" in query.reason

    def test_the_query_id_is_stable(self) -> None:
        """同じ要求からは同じ ID が出る（重複して発しないため）。"""
        first = to_query(_request(), priors={(PUMP, RUNNING): 0.5}, means=MEANS)
        second = to_query(_request(), priors={(PUMP, RUNNING): 0.5}, means=MEANS)
        assert first is not None
        assert second is not None
        assert first.query_id == second.query_id


class TestToQueries:
    """列の変換。"""

    def test_convertible_and_skipped_are_separated(self) -> None:
        """変換できた分と落ちた分を分けて返す。

        落とした件数を返さないと、要求が積まれたまま消えたことが分からない。
        """
        requests = [_request(fluent=RUNNING), _request(fluent=FAULTED)]
        queries, skipped = to_queries(requests, priors={(PUMP, RUNNING): 0.5}, means=MEANS)
        assert len(queries) == 1
        assert len(skipped) == 1
        assert skipped[0].request.fluent == FAULTED

    def test_the_skip_reason_is_recorded(self) -> None:
        """落とした理由が残る。"""
        _queries, skipped = to_queries([_request()], priors={}, means=MEANS)
        assert "推測で埋めない" in skipped[0].reason

    def test_an_empty_input_yields_empty_outputs(self) -> None:
        """空は空（例外にしない）。"""
        queries, skipped = to_queries([], priors={}, means=MEANS)
        assert queries == []
        assert skipped == []

    def test_all_convertible_leaves_nothing_skipped(self) -> None:
        """全部変換できれば落ちた分は空（陰性対照）。"""
        queries, skipped = to_queries(
            [_request(fluent=RUNNING), _request(fluent=FAULTED)],
            priors={(PUMP, RUNNING): 0.5, (PUMP, FAULTED): 0.1},
            means=MEANS,
        )
        assert len(queries) == 2
        assert skipped == []
