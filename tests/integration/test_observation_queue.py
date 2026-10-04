"""追加観測の要求の作業キュー（M8-05、ADR-0011）。

**ここでしか確かめられない。** `ObservationQueue` は SQL（UPSERT の条件・部分索引を使う
未充足の絞り込み）そのもので、単体テストの代役では「満たされた要求を未充足に戻さない」
「行を消さない」が本当に効いているか分からない。

重点:

1. **同じ不一致・同じフルーエントは 1 行** — 照合は周期ごとに回るので、同じ要求が繰り返し出る。
2. **満たされた要求を未充足に戻さない** — 再度要求が出ても observation は済んでいる。
3. **行を消さない** — 「要求したが満たされなかった」履歴が残る。
4. **発行は充足ではない** — `issued_at` が立っても候補に残る（結果が戻るとは限らない）。
"""

from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest

from sbp.core.config import Settings
from sbp.core.time import Interval, now
from sbp.knowledge import ObservationQueue, ObservationRow

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

PUMP = "https://sbp.example/site/siteA/asset/P-101"
LINE = "https://sbp.example/site/siteA/asset/LINE-1"
RUNNING = "https://sbp.example/ent#Running"
FAULTED = "https://sbp.example/ent#Faulted"
SITE = "siteQueueTest"


def _row(
    *,
    entity: str = PUMP,
    fluent: str = RUNNING,
    utility: float = 0.9,
    discrepancy_id: str = "d-1",
    reason: str = "判断できない",
) -> ObservationRow:
    start = now().replace(microsecond=0) - timedelta(hours=1)
    return ObservationRow(
        entity=entity,
        fluent=fluent,
        interval=Interval(start, start + timedelta(minutes=10)),
        reason=reason,
        utility=utility,
        discrepancy_id=discrepancy_id,
    )


class TestEnqueueAndRead:
    """積む・読む。"""

    @pytest.mark.asyncio
    async def test_an_enqueued_request_comes_back(self, settings: Settings) -> None:
        """積んだ要求が未充足として返る（区間と理由も保たれる）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = ObservationQueue(connection, site=SITE)
            row = _row()
            assert await queue.enqueue([row]) == 1

            outstanding = await queue.outstanding()
            assert len(outstanding) == 1
            assert outstanding[0] == row
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_results_are_ordered_by_utility(self, settings: Settings) -> None:
        """効用の高い順に返る（スケジューラが上から評価する）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = ObservationQueue(connection, site=SITE)
            await queue.enqueue(
                [
                    _row(utility=0.2, discrepancy_id="d-low"),
                    _row(utility=0.9, discrepancy_id="d-high"),
                    _row(utility=0.5, discrepancy_id="d-mid"),
                ]
            )

            utilities = [row.utility for row in await queue.outstanding()]
            assert utilities == [0.9, 0.5, 0.2]
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_the_same_request_stays_one_row(self, settings: Settings) -> None:
        """同じ不一致・同じフルーエントの要求は 1 行にまとまる。

        照合は周期ごとに回るので、解決されない不一致からは同じ要求が繰り返し出る。毎回
        新しい行を作ると、キューが周期の回数だけ膨らみ、同じ観測を何度も選ぶ。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = ObservationQueue(connection, site=SITE)
            await queue.enqueue([_row(utility=0.3)])
            await queue.enqueue([_row(utility=0.8, reason="深刻度が上がった")])

            outstanding = await queue.outstanding()
            assert len(outstanding) == 1
            assert outstanding[0].utility == 0.8, "効用は更新される"
            assert outstanding[0].reason == "深刻度が上がった"
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_different_fluents_are_different_rows(self, settings: Settings) -> None:
        """同じ不一致でもフルーエントが違えば別の要求。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = ObservationQueue(connection, site=SITE)
            await queue.enqueue(
                [
                    _row(fluent=RUNNING, discrepancy_id="d-1"),
                    _row(fluent=FAULTED, discrepancy_id="d-1"),
                ]
            )

            assert len({row.fluent for row in await queue.outstanding()}) == 2
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_other_sites_are_not_returned(self, settings: Settings) -> None:
        """他拠点の要求は返らない（1 プロセス 1 拠点）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            await ObservationQueue(connection, site=SITE).enqueue([_row()])

            other = ObservationQueue(connection, site=f"{SITE}-other")
            assert await other.outstanding() == []
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_the_limit_is_respected(self, settings: Settings) -> None:
        """上限で打ち切る（効用計算が件数に比例するため）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = ObservationQueue(connection, site=SITE)
            await queue.enqueue([_row(discrepancy_id=f"d-{index}") for index in range(5)])

            assert len(await queue.outstanding(limit=2)) == 2
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_an_open_interval_survives(self, settings: Settings) -> None:
        """まだ終わっていない区間（``valid_to`` なし）も往復する。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = ObservationQueue(connection, site=SITE)
            start = now().replace(microsecond=0)
            await queue.enqueue(
                [
                    ObservationRow(
                        entity=LINE,
                        fluent=RUNNING,
                        interval=Interval(start, None),
                        reason="継続中",
                        utility=0.5,
                        discrepancy_id="d-open",
                    )
                ]
            )

            outstanding = await queue.outstanding()
            assert outstanding[0].interval.end is None
            await connection.rollback()


class TestLifecycle:
    """発行と充足。"""

    @pytest.mark.asyncio
    async def test_issuing_does_not_satisfy(self, settings: Settings) -> None:
        """発行しても未充足のまま残る。

        発行は「頼んだ」であって「分かった」ではない。候補から外すと、結果が戻ってこなかった
        要求が永久に満たされない。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = ObservationQueue(connection, site=SITE)
            await queue.enqueue([_row()])
            await queue.mark_issued(discrepancy_id="d-1", fluent=RUNNING, query_id="q-1", at=now())

            assert len(await queue.outstanding()) == 1
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_a_satisfied_request_leaves_the_queue(self, settings: Settings) -> None:
        """充足した要求は候補から外れる。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = ObservationQueue(connection, site=SITE)
            await queue.enqueue([_row()])
            await queue.mark_satisfied(discrepancy_id="d-1", fluent=RUNNING, at=now())

            assert await queue.outstanding() == []
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_a_satisfied_row_is_not_deleted(self, settings: Settings) -> None:
        """充足しても行は残る（要求したことの履歴）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = ObservationQueue(connection, site=SITE)
            await queue.enqueue([_row()])
            await queue.mark_satisfied(discrepancy_id="d-1", fluent=RUNNING, at=now())

            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT satisfied_at FROM observation_requests "
                    "WHERE site = %s AND request_id = %s",
                    (SITE, f"d-1:{RUNNING}"),
                )
                rows = await cursor.fetchall()
            assert len(rows) == 1, "行が消えている（履歴が失われる）"
            assert rows[0][0] is not None
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_a_satisfied_request_is_not_reopened(self, settings: Settings) -> None:
        """充足した要求は再度積まれても未充足に戻らない。

        同じ不一致から再度要求が出ても、観測は済んでいる（不一致が消えないのは観測の問題
        ではない）。戻すと同じ観測を無限に繰り返す。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = ObservationQueue(connection, site=SITE)
            await queue.enqueue([_row()])
            await queue.mark_satisfied(discrepancy_id="d-1", fluent=RUNNING, at=now())
            await queue.enqueue([_row(utility=1.0)])

            assert await queue.outstanding() == []
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_marking_satisfied_twice_keeps_the_first_time(self, settings: Settings) -> None:
        """充足時刻を上書きしない（最初に満たされた時刻が残る）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = ObservationQueue(connection, site=SITE)
            await queue.enqueue([_row()])
            first = now().replace(microsecond=0)
            await queue.mark_satisfied(discrepancy_id="d-1", fluent=RUNNING, at=first)
            await queue.mark_satisfied(
                discrepancy_id="d-1", fluent=RUNNING, at=first + timedelta(hours=1)
            )

            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT satisfied_at FROM observation_requests WHERE request_id = %s",
                    (f"d-1:{RUNNING}",),
                )
                row = await cursor.fetchone()
            assert row is not None
            assert row[0] == first
            await connection.rollback()
