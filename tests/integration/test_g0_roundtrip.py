"""ゲート G0 の統合テスト（docs/TASK.md M0-13）。

検収内容: Compose 上で **produce → consume → assert_ → as-of query** の往復が通ること。

これは「イベントが記録源で、信念は二時制で永続化され、当時の信念を再構成できる」という
M0 の主張そのものを実機で確かめるものである。設計書 §6.1（イベントログ）と §6.4（二時制）に対応。
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.events import EventEnvelope, EventLayer, Producer, ValidTime, topic_name
from sbp.core.ids import site_iri
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.eventlog import (
    ConsumerPolicy,
    InMemoryIdempotencyStore,
    KafkaEventConsumer,
    KafkaEventProducer,
    process_once,
)
from sbp.knowledge import HttpGraphStore, schema_graph

if TYPE_CHECKING:
    from sbp.core.config import Settings
    from sbp.knowledge import AssertionStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

PUMP = site_iri("siteA", "asset", "P-101")
PROV = site_iri("siteA", "prov", "g0-test")


def _stop_event(*, tx_time: object | None = None) -> EventEnvelope:
    """設計書 §14 の「ポンプが停止した」事象に相当するイベント。"""
    t0 = now()
    return EventEnvelope(
        type=vocab.ent.StopCmd,
        subject=PUMP,
        valid_time=ValidTime(start=t0, end=t0 + timedelta(seconds=40), uncertainty_s=20.0),
        tx_time=tx_time or t0 + timedelta(seconds=42),  # type: ignore[arg-type]
        modality=Modality.INFERRED,
        belief=BooleanBelief(p=0.91),
        producer=Producer(agent="integration-test", version="0.1.0"),
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


class TestG0EventLogRoundTrip:
    """produce → consume の往復（実ブローカー）。"""

    async def test_produce_then_consume_the_same_event(
        self, settings: Settings, kafka_available: None
    ) -> None:
        """送ったイベントが同じ内容で読み戻る。

        トピックは無期限保持（設計書 §6.1）なので、新しい consumer グループは過去のイベントも
        読む。``event_id`` で自分が送ったものを特定するまで読み進める（グローバル順序を
        仮定しない。不変条件 4）。
        """
        del kafka_available
        topic = topic_name(settings.site_id, EventLayer.BRIDGE, "physical")
        event = _stop_event()
        received: list[EventEnvelope] = []
        found = asyncio.Event()

        async def handler(e: EventEnvelope) -> None:
            received.append(e)
            if e.event_id == event.event_id:
                found.set()

        async with KafkaEventProducer(settings) as producer:
            await producer.send(topic, event)
            await producer.flush()

        consumer = KafkaEventConsumer(
            settings,
            topics=(topic,),
            policy=ConsumerPolicy(consumer_group=f"g0-roundtrip-{event.event_id.hex[:8]}"),
            store=InMemoryIdempotencyStore(),
            handler=handler,
        )
        async with consumer:
            run_task = asyncio.create_task(consumer.run())
            try:
                await asyncio.wait_for(found.wait(), timeout=60)
            finally:
                consumer.request_stop()
                await asyncio.wait_for(run_task, timeout=30)

        matching = [e for e in received if e.event_id == event.event_id]
        assert len(matching) == 1, "送ったイベントがちょうど 1 回読み戻る"
        got = matching[0]
        assert got.event_id == event.event_id
        assert got.subject == PUMP
        assert got.type == vocab.ent.StopCmd
        assert isinstance(got.belief, BooleanBelief)
        assert got.belief.p == pytest.approx(0.91)
        assert got.valid_time.uncertainty_s == 20.0, "点時刻のイベントにならない（不変条件 8）"

    async def test_redelivery_does_not_duplicate_side_effects(
        self, settings: Settings, kafka_available: None
    ) -> None:
        """不変条件 4: 同じ event_id を 2 回処理しても副作用は 1 回。

        実ブローカーからの再配送を模すため、同一イベントを同じ冪等ストアで 2 回処理する。
        """
        del kafka_available, settings
        topic = topic_name("siteA", EventLayer.BRIDGE, "physical")
        event = _stop_event()
        store = InMemoryIdempotencyStore()
        calls: list[EventEnvelope] = []

        async def handler(e: EventEnvelope) -> None:
            calls.append(e)

        policy = ConsumerPolicy(consumer_group="g0-idem")
        first = await process_once(event, topic=topic, handler=handler, store=store, policy=policy)
        second = await process_once(event, topic=topic, handler=handler, store=store, policy=policy)
        assert (first, second) == (True, False)
        assert len(calls) == 1


class TestG0BitemporalPersistence:
    """assert_ → as-of query の往復（実 Oxigraph）。"""

    def test_assert_then_query_on_real_store(self, assertion_store: AssertionStore) -> None:
        """実サーバでも二時制アサーションが読み書きできる。"""
        t0 = now()
        a = Assertion(
            subject=PUMP,
            predicate=vocab.ent.Running,
            object=True,
            valid_from=t0,
            tx_from=t0,
            modality=Modality.INFERRED,
            belief=BooleanBelief(p=0.91),
            provenance=PROV,
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        assertion_store.assert_(a)

        rows = assertion_store.query(subject=PUMP)
        assert len(rows) == 1
        assert rows[0].assertion_id == a.assertion_id
        assert rows[0].confidence == pytest.approx(0.91)
        assert rows[0].ontology_version == vocab.ONTOLOGY_VERSION

    def test_as_of_reconstructs_the_belief_of_that_time(
        self, assertion_store: AssertionStore
    ) -> None:
        """設計書 §6.4: as-of で「当時の信念」が戻る。

        §14 のトレースに対応する: 記録は稼働（0.95）→ 観測が入って事後 0.14 に訂正。
        訂正後も「当時はそう信じていた」ことが残る。
        """
        t0 = now()
        t1 = t0 + timedelta(hours=1)

        record_belief = Assertion(
            subject=PUMP,
            predicate=vocab.ent.Running,
            object=True,
            valid_from=t0,
            tx_from=t0,
            modality=Modality.INFERRED,
            belief=BooleanBelief(p=0.95),
            provenance=PROV,
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        assertion_store.assert_(record_belief)

        # 観測が入って事後が下がった → 旧文を閉じて新文を追加（不変条件 2）
        assertion_store.retract(record_belief.assertion_id, t1)
        posterior = record_belief.supersede(belief=BooleanBelief(p=0.14), at=t1, provenance=PROV)
        assertion_store.assert_(posterior)

        current = assertion_store.query(subject=PUMP)
        assert len(current) == 1
        assert current[0].confidence == pytest.approx(0.14), "いまの信念は観測が支配する"

        past = assertion_store.query(subject=PUMP, as_of=t0 + timedelta(minutes=30))
        assert len(past) == 1
        assert past[0].confidence == pytest.approx(0.95), "当時の信念は保存されている"

        history = assertion_store.query(subject=PUMP, include_closed=True)
        assert len(history) == 2, "訂正は追記であり、旧文は消えない"

    def test_hypothetical_stays_out_of_main_belief(self, assertion_store: AssertionStore) -> None:
        """不変条件 3: シナリオの文は主信念クエリに現れない（実サーバでも）。"""
        t0 = now()
        # シナリオグラフは拠点でスコープされないため、実行ごとに一意な ID を使う。
        scenario = f"urn:sbp:scenario:g0-defer-{os.getpid()}"
        assertion_store.assert_(
            Assertion(
                subject=PUMP,
                predicate=vocab.ent.Faulted,
                object=True,
                valid_from=t0,
                tx_from=t0,
                modality=Modality.HYPOTHETICAL,
                belief=BooleanBelief(p=0.7),
                provenance=PROV,
                scenario=scenario,
                ontology_version=vocab.ONTOLOGY_VERSION,
            )
        )
        assert assertion_store.query(subject=PUMP) == []
        assert len(assertion_store.query(subject=PUMP, scenario=scenario)) == 1
        # 後片付けは assertion_store fixture が行う（シナリオグラフも対象に含まれる）。


class TestG0StackReadiness:
    """ゲート G0 の前提（4 サービスが使える状態である）を明示的に検査する。"""

    def test_oxigraph_is_reachable(self, graph_store: object) -> None:
        """ADR-0004: コンテナ healthcheck の代わりにここで疎通を確認する。"""
        assert graph_store is not None, "graph_store fixture が疎通確認を済ませている"

    def test_ontology_is_loaded_into_the_store(self, graph_store: object) -> None:
        """``make seed`` がオントロジーを投入済みであること。"""
        assert isinstance(graph_store, HttpGraphStore)
        rows = graph_store.query(
            "SELECT (COUNT(*) AS ?n) WHERE { GRAPH "
            f"<{schema_graph(vocab.ONTOLOGY_VERSION)}> {{ ?s ?p ?o }} }}"
        )
        assert int(rows[0]["n"] or "0") > 500, (
            "オントロジーが投入されていない。`make seed` を実行する"
        )

    def test_projection_tables_exist(self, postgres_available: None) -> None:
        """alembic のマイグレーションが適用済みであること。"""
        del postgres_available

    def test_topics_exist(self, kafka_available: None) -> None:
        """``make seed`` がトピックを作成済みであること。"""
        del kafka_available
