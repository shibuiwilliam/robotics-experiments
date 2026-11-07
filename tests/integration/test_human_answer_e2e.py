"""人の確認回答を、API コマンドから信念まで実物のサービス上で通す（M8-29-4、ADR-0018）。

外界（人）は API のコマンドハンドラを呼ぶだけで、基盤の内側は production のコードで通す:

    コマンド ``answer_observation`` → 判断キュー → learning-worker（``build_worker``）
        → ``<site>.integration.business``（主張イベント）→ belief-worker（``build_worker``）
        → ``inferred`` の信念

確かめること:

1. 人の「稼働している」が信念を引き上げ、観測要求が満たされる。
2. 同じコマンドの再送は二重に適用されない（判断キューの冪等）。
3. 存在しない要求への回答は失敗として残り、イベントは送られない。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic
from services.belief_worker import build_worker as build_belief_worker
from services.learning_worker import build_worker as build_learning_worker

from sbp.api.commands import build_command_handler
from sbp.core import vocab
from sbp.core.events import EventLayer, topic_name
from sbp.core.ids import site_iri
from sbp.core.modality import Modality
from sbp.core.time import Interval, now
from sbp.knowledge import AssertionStore, DecisionQueue, ObservationQueue, ObservationRow

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

    from sbp.core.config import Settings
    from sbp.knowledge import HttpGraphStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

DEADLINE_S = 30.0
RUNNING = str(vocab.ent.Running)
OPERATOR = "https://sbp.example/person/operator-e2e"


@pytest.fixture
def site(test_graph_suffix: str, assertion_store: AssertionStore) -> str:
    """テストごとの拠点（``assertion_store`` は後片付けのために要求する）。"""
    del assertion_store
    return f"{test_graph_suffix}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def topics(site: str, settings: Settings, kafka_available: None) -> Iterator[None]:
    """belief-worker が購読する 2 つのトピックを作る（テスト後に消す）。"""
    del kafka_available
    names = [
        topic_name(site, EventLayer.INTEGRATION, "business"),
        topic_name(site, EventLayer.BRIDGE, "physical"),
    ]
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
    for future in admin.create_topics(
        [NewTopic(name, num_partitions=1, replication_factor=1) for name in names]
    ).values():
        future.result(timeout=20)
    yield
    for future in admin.delete_topics(names).values():
        with suppress(Exception):
            future.result(timeout=20)


@pytest.fixture
async def connection(
    settings: Settings, site: str, postgres_available: None
) -> AsyncIterator[psycopg.AsyncConnection[tuple[Any, ...]]]:
    del postgres_available
    conn = await psycopg.AsyncConnection.connect(settings.pg_dsn, autocommit=True)
    yield conn
    for table in ("learning_decisions", "observation_requests"):
        await conn.execute(f"DELETE FROM {table} WHERE site = %s", (site,))  # type: ignore[arg-type]
    await conn.close()


async def _until(condition: Callable[[], bool], step: Callable[[], Awaitable[Any]]) -> bool:
    deadline = time.monotonic() + DEADLINE_S
    while not condition() and time.monotonic() < deadline:
        await step()
    return condition()


def _command(
    site: str, *, decision_id: str = "a-1", discrepancy_id: str = "expectation:e2e"
) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "discrepancy_id": discrepancy_id,
        "fluent": RUNNING,
        "entity": site_iri(site, "asset", "P-101"),
        "state": True,
        "note": "現場で稼働中を目視した",
        "answered_by": OPERATOR,
    }


async def _seed_request(
    connection: psycopg.AsyncConnection[tuple[Any, ...]], site: str
) -> ObservationRow:
    start = now().replace(microsecond=0) - timedelta(hours=1)
    row = ObservationRow(
        entity=site_iri(site, "asset", "P-101"),
        fluent=RUNNING,
        interval=Interval(start, start + timedelta(minutes=30)),
        reason="期待の期限切れ: 判定する観測が無い",
        utility=0.7,
        discrepancy_id="expectation:e2e",
    )
    await ObservationQueue(connection, site=site).enqueue([row])
    return row


class TestHumanAnswerPath:
    @pytest.mark.usefixtures("topics")
    async def test_an_answer_reaches_the_belief_and_satisfies_the_request(
        self,
        settings: Settings,
        site: str,
        graph_store: HttpGraphStore,
        connection: psycopg.AsyncConnection[tuple[Any, ...]],
    ) -> None:
        run_settings = settings.model_copy(update={"site_id": site})
        pump = site_iri(site, "asset", "P-101")
        await _seed_request(connection, site)

        # 外界: 人がコマンドを送る（再送しても 1 回だけ適用される）
        queue = DecisionQueue(connection, site=site)
        handler = build_command_handler(queue.enqueue)
        handler("answer_observation", _command(site))
        handler("answer_observation", _command(site))
        await asyncio.sleep(0.2)
        assert len(await queue.pending()) == 1, "再送は積まれない"

        learning, _decisions = await build_learning_worker(run_settings, site_id=site)
        try:
            assert await learning.step() == 1
        finally:
            await learning.aclose()
        assert await ObservationQueue(connection, site=site).outstanding() == []

        belief, consumer = await build_belief_worker(run_settings, site_id=site)
        try:
            assert await _until(lambda: belief.applied_total >= 1, belief.step)
        finally:
            await consumer.stop()

        rows = AssertionStore(graph_store, site_id=site).query(
            subject=pump, predicate=RUNNING, modality=Modality.INFERRED
        )
        assert rows, "回答が信念になっていない"
        assert max(row.confidence for row in rows) > 0.9

    @pytest.mark.usefixtures("topics")
    async def test_an_answer_to_an_unknown_request_fails_and_sends_nothing(
        self,
        settings: Settings,
        site: str,
        connection: psycopg.AsyncConnection[tuple[Any, ...]],
    ) -> None:
        run_settings = settings.model_copy(update={"site_id": site})
        queue = DecisionQueue(connection, site=site)
        handler = build_command_handler(queue.enqueue)
        handler("answer_observation", _command(site, discrepancy_id="expectation:nobody-asked"))
        await asyncio.sleep(0.2)

        learning, _decisions = await build_learning_worker(run_settings, site_id=site)
        try:
            assert await learning.step() == 0
        finally:
            await learning.aclose()

        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT error FROM learning_decisions WHERE site = %s AND decision_id = 'a-1'",
                (site,),
            )
            found = await cursor.fetchone()
        assert found is not None
        assert "no observation request" in str(found[0])
        assert await queue.pending() == []
