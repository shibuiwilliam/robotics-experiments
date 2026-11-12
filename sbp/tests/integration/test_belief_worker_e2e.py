"""信念更新ワーカーの端から端まで（Redpanda → 前向きフィルタ → Oxigraph）。

**これまで検査していなかったのは「実物を繋いだときに動くか」だけである。** 変換（ingest）と
ループの性質（worker）は単体テストが押さえているが、実際の consumer・実際の冪等ストア・
実際のグラフストアを繋いだ経路は一度も通っていなかった。

ここで確かめること:

1. Redpanda に流したプローブイベントが、Oxigraph の ``inferred`` アサーションとして現れる。
2. **同じイベントを 2 度流しても信念が二重に進まない**（不変条件 4。冪等ストアは Postgres 版で、
   メモリ版だと再起動のたびに全件を再処理する）。
3. ハンドラが失敗したら**冪等記録が取り消され、再配送で再試行できる状態に戻る**
   ——「処理済みなのに信念に入っていない」状態を作らない。
4. 書いたアサーションが二時制クエリで引ける（``as_of`` / ``valid_at``）。
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic
from services.belief_worker import BeliefWorker, default_ingest, kafka_driver

from sbp.core import vocab
from sbp.core.belief import BooleanBelief
from sbp.core.events import (
    EventEnvelope,
    EventLayer,
    ProbeEvidence,
    Producer,
    ValidTime,
    topic_name,
)
from sbp.core.ids import new_event_id
from sbp.core.modality import Modality
from sbp.eventlog.consumer import ConsumerPolicy, KafkaEventConsumer
from sbp.eventlog.idempotency import PostgresIdempotencyStore
from sbp.eventlog.producer import KafkaEventProducer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence

    from sbp.core.config import Settings
    from sbp.knowledge import AssertionStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

T0 = datetime(2026, 6, 10, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"


@pytest.fixture
def topic(kafka_available: None, settings: Settings) -> Iterator[str]:
    """テストごとに独立したトピックを作る。

    **共有トピック（``siteA.bridge.physical``）は使えない。** consumer は
    ``auto_offset_reset="earliest"`` なので、新しい consumer グループは**トピックの履歴を
    最初から読む**。他のテストが流したイベントが先に届き、``max_events`` の枠を食い潰す
    （実際にそれで落ちて気付いた——``StopCmd`` の古いイベントが読まれていた）。

    トピック名は ``topic_name()`` で組み立てるので命名規約は本番と同じ経路を通る。
    """
    del kafka_available
    name = topic_name(f"it-{uuid.uuid4().hex[:8]}", EventLayer.BRIDGE, "physical")
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
    created = admin.create_topics([NewTopic(name, num_partitions=1, replication_factor=1)])
    created[name].result(timeout=20)
    yield name
    for future in admin.delete_topics([name]).values():
        with suppress(Exception):  # 後片付けの失敗でテスト結果を変えない
            future.result(timeout=20)


def _event(*, at: datetime, p: float, event_id: uuid.UUID | None = None) -> EventEnvelope:
    """プローブ由来の昇格イベント（様相は inferred、不変条件 3）。"""
    return EventEnvelope(
        event_id=event_id if event_id is not None else new_event_id(),
        type="sbp-core:PhysicalEvent/ent:Running",
        subject=PUMP,
        valid_time=ValidTime(start=at, uncertainty_s=5.0),
        tx_time=at,
        modality=Modality.INFERRED,
        belief=BooleanBelief(p=p),
        evidence=(ProbeEvidence(probe_id="https://sbp.example/probe/Running/1.0.0"),),
        producer=Producer(agent="bridge@siteA", version="0.1.0"),
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


@pytest.fixture
def consumer_group() -> str:
    """テストごとに別の consumer グループ。

    **グループを共有すると、前のテストが処理したイベントが「処理済み」として飛ばされる**
    ——冪等性が効いているのかテストが壊れているのか区別できなくなる。
    """
    return f"it-belief-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def pg_connection(
    settings: Settings, postgres_available: None
) -> AsyncIterator[psycopg.AsyncConnection[tuple[Any, ...]]]:
    """冪等ストア用の接続。テスト後にそのグループの記録を消す。"""
    del postgres_available
    connection = await psycopg.AsyncConnection.connect(settings.pg_dsn, autocommit=True)
    yield connection
    await connection.close()


async def _publish(settings: Settings, topic: str, events: Sequence[EventEnvelope]) -> None:
    """イベントを実ブローカーに流す。"""
    async with KafkaEventProducer(settings) as producer:
        for event in events:
            await producer.send(topic, event)


async def _run_worker(
    settings: Settings,
    *,
    topic: str,
    store: AssertionStore,
    connection: psycopg.AsyncConnection[tuple[Any, ...]],
    group: str,
    expect: int,
) -> BeliefWorker:
    """ワーカーを実物の consumer で ``expect`` 件分だけ回す。

    ハンドラの同一性に注意する（``build_worker`` と同じ順序）: ワーカーを先に作り、
    その ``handle_event`` を consumer に渡す。
    """
    worker = BeliefWorker(settings, sink=store.assert_, ingest=default_ingest(settings))
    consumer = KafkaEventConsumer(
        settings,
        topics=(topic,),
        policy=ConsumerPolicy(consumer_group=group, max_retries=0),
        store=PostgresIdempotencyStore(connection),
        handler=worker.handle_event,
    )
    await consumer.start()
    worker.attach_driver(kafka_driver(consumer))
    try:
        # 1 ループで expect 件を取りに行く（driver は batch_size を上限に渡す）
        await consumer.run(max_events=expect)
    finally:
        await consumer.stop()
    return worker


class TestEndToEnd:
    """Redpanda → 信念更新 → Oxigraph。"""

    async def test_events_become_bitemporal_assertions(
        self,
        settings: Settings,
        assertion_store: AssertionStore,
        topic: str,
        pg_connection: psycopg.AsyncConnection[tuple[Any, ...]],
        consumer_group: str,
    ) -> None:
        """流したイベントが二時制アサーションとして引ける。"""
        events = [_event(at=T0 + timedelta(seconds=10 * i), p=0.95) for i in range(3)]
        await _publish(settings, topic, events)

        worker = await _run_worker(
            settings,
            topic=topic,
            store=assertion_store,
            connection=pg_connection,
            group=consumer_group,
            expect=3,
        )
        assert worker.applied_total == 3

        rows = assertion_store.query(
            subject=PUMP,
            predicate=str(vocab.ent.Running),
            modality=Modality.INFERRED,
        )
        assert len(rows) == 3
        # 不変条件 1: 版を欠かさない。不変条件 3: プローブ由来は inferred
        assert all(row.ontology_version == vocab.ONTOLOGY_VERSION for row in rows)
        assert all(row.modality is Modality.INFERRED for row in rows)
        # 不変条件 7: 較正済みの確信度が入っている
        assert all(0.0 <= row.confidence <= 1.0 for row in rows)
        # 来歴から元のイベントが辿れる
        event_ids = {str(event.event_id) for event in events}
        assert all(any(event_id in row.provenance for event_id in event_ids) for row in rows)

        # 二時制で引ける: 記録前の as_of では何も返らない（まだ信じていなかった）
        before = assertion_store.query(
            subject=PUMP,
            predicate=str(vocab.ent.Running),
            modality=Modality.INFERRED,
            as_of=T0 - timedelta(days=1),
        )
        assert before == []
        # 有効時刻で絞ると、その時刻について述べている文だけが返る
        at_first = assertion_store.query(
            subject=PUMP,
            predicate=str(vocab.ent.Running),
            modality=Modality.INFERRED,
            valid_at=T0,
        )
        assert len(at_first) == 1
        assert at_first[0].valid_from == T0

    async def test_the_filter_advances_across_the_real_stream(
        self,
        settings: Settings,
        assertion_store: AssertionStore,
        topic: str,
        pg_connection: psycopg.AsyncConnection[tuple[Any, ...]],
        consumer_group: str,
    ) -> None:
        """前向きフィルタが実ストリーム上でも進む（1 件目より後の事後が高い）。

        単体テストと同じ性質だが、**実際の consumer が実体単位の順序を保っていること**まで
        含めて確かめている。順序が崩れていれば「時刻が戻った」で捨てられて件数が合わない。
        """
        count = 8
        await _publish(
            settings,
            topic,
            [_event(at=T0 + timedelta(seconds=10 * i), p=0.95) for i in range(count)],
        )
        worker = await _run_worker(
            settings,
            topic=topic,
            store=assertion_store,
            connection=pg_connection,
            group=consumer_group,
            expect=count,
        )
        assert worker.applied_total == count
        assert worker.ingest.skipped == {}

        rows = sorted(
            assertion_store.query(
                subject=PUMP, predicate=str(vocab.ent.Running), modality=Modality.INFERRED
            ),
            key=lambda row: row.valid_from,
        )
        assert len(rows) == count
        assert rows[-1].confidence > rows[0].confidence


class TestIdempotency:
    """不変条件 4。"""

    async def test_replaying_the_same_event_does_not_advance_the_belief_twice(
        self,
        settings: Settings,
        assertion_store: AssertionStore,
        topic: str,
        pg_connection: psycopg.AsyncConnection[tuple[Any, ...]],
        consumer_group: str,
    ) -> None:
        """同じ ``event_id`` を 2 度流しても 1 回しか効かない。

        **冪等ストアは Postgres 版**である。メモリ版だとプロセスをまたいだ重複を検出できず、
        再起動のたびに全イベントを再処理して信念が二重に進む。
        """
        event_id = new_event_id()
        duplicate = _event(at=T0, p=0.95, event_id=event_id)
        # 同一の event_id で 2 回送る（producer 側の冪等化だけでは防げない経路）
        await _publish(settings, topic, [duplicate, duplicate])

        worker = await _run_worker(
            settings,
            topic=topic,
            store=assertion_store,
            connection=pg_connection,
            group=consumer_group,
            expect=2,
        )
        # 2 件取得したが、副作用は 1 回だけ
        assert worker.applied_total == 1

        rows = assertion_store.query(
            subject=PUMP, predicate=str(vocab.ent.Running), modality=Modality.INFERRED
        )
        assert len(rows) == 1

    async def test_a_failing_handler_releases_the_idempotency_record(
        self,
        settings: Settings,
        topic: str,
        pg_connection: psycopg.AsyncConnection[tuple[Any, ...]],
        consumer_group: str,
    ) -> None:
        """ハンドラが失敗したら冪等記録が取り消され、再配送で再試行できる。

        **これが「ハンドラが本体の仕事をする」ことの意味である。** バッファに溜めるだけの
        ハンドラにすると、溜めた直後に落ちたイベントは「処理済みなのに信念に入っていない」
        状態で永久に失われる。
        """
        event = _event(at=T0, p=0.95)
        await _publish(settings, topic, [event])

        def explode(_assertion: object) -> None:
            raise RuntimeError("書き込み先が落ちている")

        worker = BeliefWorker(settings, sink=explode, ingest=default_ingest(settings))
        consumer = KafkaEventConsumer(
            settings,
            topics=(topic,),
            policy=ConsumerPolicy(consumer_group=consumer_group, max_retries=0),
            store=PostgresIdempotencyStore(pg_connection),
            handler=worker.handle_event,
        )
        await consumer.start()
        try:
            with pytest.raises(RuntimeError, match="書き込み先が落ちている"):
                await consumer.run(max_events=1)
        finally:
            await consumer.stop()

        # 冪等記録が残っていないこと（残っていると再配送でも飛ばされ、信念が永久に欠ける）
        async with pg_connection.cursor() as cursor:
            await cursor.execute(
                "SELECT count(*) FROM processed_events "
                "WHERE consumer_group LIKE %s AND event_id = %s",
                (f"%{consumer_group}", event.event_id),
            )
            row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 0
