"""統合テスト: 記録パスを頭から通す（M8-25-6、ADR-0016、所見 101 / 115）。

外界（業務システム）だけをテストが置き、基盤の内側は production のコードで通す:

    SQLite のモック（設計書 §14 の台本） → ingest-worker（``build_worker``）
        → ``<site>.integration.business``（存在イベント + 主張イベント）
        → 知識グラフ（台帳のアサーション + 投影、planned の計画）
        → reconcile-worker（``build_worker``）の記録列

照合の窓は「観測の刻みが無い記録」を捨てる設計なので、物理側のイベント（``bridge.physical``）を
先に流しておく。推定パスの先頭（観測 → 潜在 → プローブ）は M8-24 で、このテストの範囲ではない。
"""

from __future__ import annotations

import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic
from services.belief_worker import build_worker as build_belief_worker
from services.ingest_worker import TABLES
from services.ingest_worker import build_worker as build_ingest_worker
from services.reconcile_worker import build_worker as build_reconcile_worker

from sbp.core import vocab
from sbp.core.belief import BooleanBelief
from sbp.core.config import IntegrationSettings
from sbp.core.events import (
    EventEnvelope,
    EventLayer,
    ProbeEvidence,
    Producer,
    ValidTime,
    topic_name,
)
from sbp.core.ids import new_event_id, site_iri
from sbp.core.modality import Modality
from sbp.eventlog import ConsumerPolicy, InMemoryIdempotencyStore, KafkaEventConsumer
from sbp.eventlog.producer import KafkaEventProducer
from sbp.integration.mock import MockBusinessSystem
from sbp.integration.mock.business_system import build_default_scenario
from sbp.knowledge import AssertionStore, IngestCursorStore, entities_of_type, part_whole_edges

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

    from sbp.core.config import Settings
    from sbp.knowledge import HttpGraphStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
DEADLINE_S = 30.0


@pytest.fixture
def site(test_graph_suffix: str, assertion_store: AssertionStore) -> str:
    """テストごとの拠点。

    ``assertion_store`` を要求するのは、後片付け（接尾辞を含むグラフを消す）のため。
    """
    del assertion_store
    return f"{test_graph_suffix}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def topics(site: str, settings: Settings, kafka_available: None) -> Iterator[dict[str, str]]:
    """この拠点の業務トピックと物理トピックを作る（テスト後に消す）。"""
    del kafka_available
    names = {
        "business": topic_name(site, EventLayer.INTEGRATION, "business"),
        "physical": topic_name(site, EventLayer.BRIDGE, "physical"),
    }
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
    for future in admin.create_topics(
        [NewTopic(name, num_partitions=1, replication_factor=1) for name in names.values()]
    ).values():
        future.result(timeout=20)
    yield names
    for future in admin.delete_topics(list(names.values())).values():
        with suppress(Exception):  # 後片付けの失敗でテスト結果を変えない
            future.result(timeout=20)


@pytest.fixture
def business_db(tmp_path: Path) -> Path:
    """外界: 業務システムのモックに設計書 §14 の台本を流し込む。"""
    path = tmp_path / "business.sqlite"
    for table in TABLES:
        with MockBusinessSystem(table=table, db_path=path) as mock:
            mock.apply(build_default_scenario(T0))
    return path


@pytest.fixture
async def ingest_settings(
    settings: Settings, site: str, business_db: Path, postgres_available: None
) -> AsyncIterator[Settings]:
    """取込ワーカーの設定。テスト後にこの拠点のカーソルと記録の投影を消す。"""
    del postgres_available
    yield settings.model_copy(
        update={
            "site_id": site,
            "integration": IntegrationSettings(mock_db_path=str(business_db), poll_period_s=1e-6),
        }
    )
    async with await psycopg.AsyncConnection.connect(settings.pg_dsn, autocommit=True) as conn:
        await conn.execute("DELETE FROM ingest_cursors WHERE site = %s", (site,))
        await conn.execute("DELETE FROM business_records WHERE site = %s", (site,))


async def _collect(settings: Settings, topic: str, *, expect: int) -> list[EventEnvelope]:
    """トピックから ``expect`` 件読む（購読の割り当てを待つので期限付きで回す）。"""
    seen: list[EventEnvelope] = []

    async def handler(event: EventEnvelope) -> None:
        seen.append(event)

    consumer = KafkaEventConsumer(
        settings,
        topics=(topic,),
        policy=ConsumerPolicy(consumer_group=f"it-ingest-{uuid.uuid4().hex[:8]}", max_retries=0),
        store=InMemoryIdempotencyStore(),
        handler=handler,
    )
    await consumer.start()
    try:
        deadline = time.monotonic() + DEADLINE_S
        while len(seen) < expect and time.monotonic() < deadline:
            await consumer.run(max_events=expect - len(seen))
    finally:
        await consumer.stop()
    return seen


async def _until(condition: Callable[[], bool], step: Callable[[], Awaitable[Any]]) -> bool:
    """``condition`` が真になるまで ``step`` を回す（期限付き）。"""
    deadline = time.monotonic() + DEADLINE_S
    while not condition() and time.monotonic() < deadline:
        await step()
    return condition()


class TestRecordPath:
    async def test_the_business_system_reaches_the_event_log_and_the_graph(
        self,
        ingest_settings: Settings,
        topics: dict[str, str],
        graph_store: HttpGraphStore,
    ) -> None:
        site = ingest_settings.site_id
        pump = site_iri(site, "asset", "P-101")
        worker, producer = await build_ingest_worker(ingest_settings, site_id=site)
        try:
            assert worker.unwired == ()
            assert await worker.step() > 0
            assert await worker.step() == 0, "2 周目は何も増えない（カーソルと差分）"
        finally:
            await producer.stop()

        # 1. 業務トピック: 業務記録 4 行の「存在」+ MES の Running の主張
        events = await _collect(ingest_settings, topics["business"], expect=5)
        assert len(events) == 5
        claims = [e for e in events if str(e.type).endswith("#Running")]
        assert [(c.subject, c.modality) for c in claims] == [(pump, Modality.OBSERVED)]
        assert claims[0].belief.p == pytest.approx(0.95 / 1.20)  # type: ignore[union-attr]

        # 2. 台帳の投影: 接地対象と部分−全体の結合が台帳の設備を返す
        assert entities_of_type(graph_store, types=(vocab.core.Asset,), site=site) == [
            pump,
            site_iri(site, "asset", "P-102"),
        ]
        line = site_iri(site, "asset", "LINE-3")
        assert {edge for edge in part_whole_edges(graph_store) if edge[1] == line} == {
            (pump, line),
            (site_iri(site, "asset", "P-102"), line),
        }

        # 3. 計画: planned の Running(P-101)
        planned = AssertionStore(graph_store, site_id=site).query(modality=Modality.PLANNED)
        assert [(row.subject, row.predicate) for row in planned] == [(pump, vocab.ent.Running)]

        # 4. カーソル: 送信の後に進んでいる
        async with await psycopg.AsyncConnection.connect(
            ingest_settings.pg_dsn, autocommit=True
        ) as conn:
            cursors = await IngestCursorStore(conn, site=site).load()
        assert cursors[(str(vocab.ent.MesSource), "operation_log")] == T0 + timedelta(minutes=5)

    async def test_the_reconcile_worker_puts_the_claim_in_the_record_column(
        self,
        ingest_settings: Settings,
        topics: dict[str, str],
    ) -> None:
        """取込の主張イベントを、production の照合ワーカーが記録列に入れる（所見 115 の契約）。"""
        site = ingest_settings.site_id
        pump = site_iri(site, "asset", "P-101")
        running = str(vocab.ent.Running)

        # 物理側（推定パスの先頭は M8-24 なので、ここではテストが置く）
        async with KafkaEventProducer(ingest_settings) as producer:
            for index in range(61):
                at = T0 + timedelta(seconds=10 * index)
                await producer.send(topics["physical"], _physical(pump, at=at))

        reconcile, consumer, reconcile_producer = await build_reconcile_worker(
            ingest_settings, site_id=site
        )
        try:
            window = reconcile.windows.window_for(entity=pump, fluent=running)
            assert await _until(lambda: len(window) == 61, reconcile.step)

            ingest, ingest_producer = await build_ingest_worker(ingest_settings, site_id=site)
            try:
                await ingest.step()
            finally:
                await ingest_producer.stop()

            def recorded() -> bool:
                series = window.to_series()
                return series is not None and series.has_records

            assert await _until(recorded, reconcile.step), "主張イベントが記録列に入っていない"
            # 記録の「存在」イベント（型は記録型の完全 IRI）はフルーエントとして読まない（所見 116）
            assert len(reconcile.windows) == 1
        finally:
            await consumer.stop()
            await reconcile_producer.stop()

    @pytest.mark.usefixtures("topics")
    async def test_the_belief_worker_takes_the_claim_as_record_evidence(
        self,
        ingest_settings: Settings,
        graph_store: HttpGraphStore,
    ) -> None:
        """取込の主張を、production の信念ワーカーが記録尤度に入れる（M8-26、所見 102）。

        業務トピックには記録の存在イベント（型が記録型）も載る。信念ワーカーが業務トピックを購読すると
        それらを読むので、**フルーエントと誤読して落ちない**こと（所見 116）も同時に確かめる。
        """
        site = ingest_settings.site_id
        pump = site_iri(site, "asset", "P-101")

        ingest, ingest_producer = await build_ingest_worker(ingest_settings, site_id=site)
        try:
            await ingest.step()
        finally:
            await ingest_producer.stop()

        belief, consumer = await build_belief_worker(ingest_settings, site_id=site)
        try:
            assert await _until(lambda: belief.applied_total >= 1, belief.step)
        finally:
            await consumer.stop()

        rows = AssertionStore(graph_store, site_id=site).query(
            subject=pump, predicate=str(vocab.ent.Running), modality=Modality.INFERRED
        )
        assert rows, "主張が信念になっていない"
        assert all(row.confidence > 0.5 for row in rows)  # MES は稼働と言っている
        assert belief.ingest.skipped, "記録の存在イベントは読み飛ばしたことが数えられている"


def _physical(pump: str, *, at: datetime) -> EventEnvelope:
    """物理観測からの推定（P-101 は動いている）。"""
    return EventEnvelope(
        event_id=new_event_id(),
        type=f"{vocab.core.PhysicalEvent}/{vocab.ent.Running}",
        subject=pump,
        valid_time=ValidTime(start=at, uncertainty_s=5.0),
        tx_time=at,
        modality=Modality.INFERRED,
        belief=BooleanBelief(p=0.98),
        evidence=(ProbeEvidence(probe_id="https://sbp.example/probe/Running/1.0.0"),),
        producer=Producer(agent="bridge@it", version="0.1.0"),
        ontology_version=vocab.ONTOLOGY_VERSION,
    )
