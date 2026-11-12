"""consumer の ``max_events`` が、取得したイベントを失わないこと（G8 で発見）。

``getmany`` を上限なしで呼ぶと、取れたぶんのうち ``max_events`` を超えた残りを処理しないまま
戻る。位置は進んでいるので、残りは二度と読まれない。ワーカーは 1 ステップに ``batch_size`` 件ずつ
読むので、バッチより多くのイベントが溜まっているとき、後ろのイベントが黙って失われていた。
"""

from __future__ import annotations

import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic

from sbp.core import vocab
from sbp.core.belief import BooleanBelief
from sbp.core.events import EventEnvelope, EventLayer, Producer, ValidTime, topic_name
from sbp.core.ids import stable_event_id
from sbp.core.modality import Modality
from sbp.eventlog import ConsumerPolicy, InMemoryIdempotencyStore, KafkaEventConsumer
from sbp.eventlog.producer import KafkaEventProducer

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sbp.core.config import Settings

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

PUMP = "https://sbp.example/site/siteA/asset/P-101"
T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
TOTAL = 250
BUDGET = 100


@pytest.fixture
def topic(settings: Settings, kafka_available: None) -> Iterator[str]:
    del kafka_available
    name = topic_name(f"it-budget-{uuid.uuid4().hex[:6]}", EventLayer.BRIDGE, "physical")
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
    for future in admin.create_topics(
        [NewTopic(name, num_partitions=1, replication_factor=1)]
    ).values():
        future.result(timeout=20)
    yield name
    for future in admin.delete_topics([name]).values():
        with suppress(Exception):
            future.result(timeout=20)


def _event(index: int) -> EventEnvelope:
    at = T0 + timedelta(seconds=index)
    return EventEnvelope(
        event_id=stable_event_id(at, f"budget|{index}"),
        type=f"{vocab.core.PhysicalEvent}/{vocab.ent.Running}",
        subject=PUMP,
        valid_time=ValidTime(start=at, uncertainty_s=1.0),
        tx_time=at,
        modality=Modality.INFERRED,
        belief=BooleanBelief(p=0.5),
        producer=Producer(agent="test", version="0"),
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


async def test_a_small_budget_never_skips_the_events_it_already_fetched(
    settings: Settings, topic: str
) -> None:
    async with KafkaEventProducer(settings) as producer:
        for index in range(TOTAL):
            await producer.send(topic, _event(index))

    seen: list[EventEnvelope] = []

    async def handler(event: EventEnvelope) -> None:
        seen.append(event)

    consumer = KafkaEventConsumer(
        settings,
        topics=(topic,),
        policy=ConsumerPolicy(consumer_group=f"it-budget-{uuid.uuid4().hex[:8]}", max_retries=0),
        store=InMemoryIdempotencyStore(),
        handler=handler,
    )
    await consumer.start()
    try:
        deadline = time.monotonic() + 30.0
        while len(seen) < TOTAL and time.monotonic() < deadline:
            await consumer.run(max_events=BUDGET)
    finally:
        await consumer.stop()

    assert len(seen) == TOTAL
    assert len({e.event_id for e in seen}) == TOTAL, "同じイベントを二度処理していない"
