"""イベントログの単体テスト（不変条件 4）。外部 I/O は Protocol でモックする。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError
from tests.constants import ONTOLOGY_VERSION, PUMP, SITE, T0

from sbp.core import vocab
from sbp.core.belief import BooleanBelief
from sbp.core.errors import SchemaValidationError
from sbp.core.events import (
    EventEnvelope,
    EventLayer,
    ProbeEvidence,
    Producer,
    ValidTime,
    topic_name,
)
from sbp.core.ids import event_id_timestamp_ms, new_event_id
from sbp.core.modality import Modality
from sbp.eventlog import (
    ConsumerPolicy,
    EventHandler,
    InMemoryEventProducer,
    InMemoryIdempotencyStore,
    process_once,
    validate_event,
)

TOPIC = topic_name(SITE, EventLayer.BRIDGE, "physical")


def _collector(sink: list[EventEnvelope]) -> EventHandler:
    """受け取ったイベントをリストに溜める async ハンドラを返す。"""

    async def handler(event: EventEnvelope) -> None:
        sink.append(event)

    return handler


def _tampered(**overrides: object) -> EventEnvelope:
    """pydantic の検証を迂回したイベントを作る（コードと schemas/ の乖離を再現する）。"""
    fields: dict[str, object] = dict(_event().__dict__)
    fields.update(overrides)
    return EventEnvelope.model_construct(None, **fields)


def _event(**overrides: object) -> EventEnvelope:
    defaults: dict[str, object] = {
        "type": vocab.ent.StopCmd,
        "subject": PUMP,
        "valid_time": ValidTime(start=T0, end=T0 + timedelta(seconds=40), uncertainty_s=20.0),
        "tx_time": T0 + timedelta(seconds=42),
        "modality": Modality.INFERRED,
        "belief": BooleanBelief(p=0.91),
        "producer": Producer(agent="bridge@siteA", version="2.4.1"),
        "ontology_version": ONTOLOGY_VERSION,
    }
    return EventEnvelope(**(defaults | overrides))  # type: ignore[arg-type]


class TestTopicNaming:
    def test_topic_follows_site_layer_class(self) -> None:
        """トピック名は <site>.<layer>.<class>（設計書 §6.1）。"""
        assert topic_name("siteA", EventLayer.REASONING, "discrepancy") == (
            "siteA.reasoning.discrepancy"
        )

    @pytest.mark.parametrize("bad", ["site.A", "site A", ""])
    def test_invalid_site_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError, match="invalid site_id"):
            topic_name(bad, EventLayer.BRIDGE, "physical")

    def test_partition_key_is_subject_iri(self) -> None:
        """順序保証の単位は実体（不変条件 4）。"""
        assert _event().partition_key == PUMP.encode("utf-8")


class TestSchemaValidationOnSend:
    async def test_valid_event_is_accepted(self) -> None:
        producer = InMemoryEventProducer()
        await producer.send(TOPIC, _event())
        assert len(producer.events_for(TOPIC)) == 1

    def test_validate_event_returns_json_bytes(self) -> None:
        payload = validate_event(_event())
        assert payload.startswith(b"{")
        assert b"event_id" in payload

    def test_schema_catches_values_that_bypassed_pydantic(self) -> None:
        """生成済みスキーマが最後の砦として機能する。

        ``model_construct`` は pydantic の検証を飛ばすため、コードの型と ``schemas/`` が
        乖離した状態を再現できる。契約の正は ``schemas/`` 側（設計書 §6.1）なので、
        型を迂回した値は送信前に止まらなければならない。
        """
        tampered = _tampered(schema_version="1.0")
        with pytest.raises(SchemaValidationError, match="does not conform"):
            validate_event(tampered)

    @pytest.mark.filterwarnings("ignore::UserWarning")
    def test_schema_catches_unknown_modality_that_bypassed_pydantic(self) -> None:
        """不変条件 3: 様相は 6 値のみ。型を迂回しても送信前に止まる。

        ``model_construct`` で enum でない値を入れるため pydantic の直列化警告が出る。
        その警告自体がここで試したいこと（型の迂回）の現れなので抑制する。
        """
        tampered = _tampered(modality="believed")
        with pytest.raises(SchemaValidationError, match="does not conform"):
            validate_event(tampered)

    def test_observed_cannot_carry_probe_evidence(self) -> None:
        """不変条件 3: プローブ出力は observed を名乗れない（pydantic 側で弾く）。"""
        with pytest.raises(ValidationError, match="cannot carry derived evidence"):
            _event(
                modality=Modality.OBSERVED,
                evidence=(ProbeEvidence(probe_id="https://sbp.example/site/siteA/probe/p1"),),
            )


class TestIdempotency:
    async def test_first_delivery_is_processed(self) -> None:
        store = InMemoryIdempotencyStore()
        seen: list[EventEnvelope] = []

        did_work = await process_once(
            _event(),
            topic=TOPIC,
            handler=_collector(seen),
            store=store,
            policy=ConsumerPolicy(consumer_group="test"),
        )
        assert did_work is True
        assert len(seen) == 1

    async def test_redelivery_is_skipped(self) -> None:
        """不変条件 4: at-least-once でも副作用は 1 回だけ。"""
        store = InMemoryIdempotencyStore()
        seen: list[EventEnvelope] = []
        event = _event()
        policy = ConsumerPolicy(consumer_group="test")

        for _ in range(3):
            await process_once(
                event, topic=TOPIC, handler=_collector(seen), store=store, policy=policy
            )

        assert len(seen) == 1, "同じ event_id の再配送で副作用が増えてはいけない"
        assert len(store) == 1

    async def test_different_consumer_groups_each_process_once(self) -> None:
        """consumer グループが違えば同じイベントを別用途で処理できる。"""
        store = InMemoryIdempotencyStore()
        event = _event()
        counts: dict[str, int] = {"a": 0, "b": 0}

        async def make(group: str) -> None:
            async def handler(_: EventEnvelope) -> None:
                counts[group] += 1

            await process_once(
                event,
                topic=TOPIC,
                handler=handler,
                store=store,
                policy=ConsumerPolicy(consumer_group=group),
            )

        await make("a")
        await make("a")
        await make("b")
        assert counts == {"a": 1, "b": 1}

    async def test_distinct_events_are_all_processed(self) -> None:
        store = InMemoryIdempotencyStore()
        seen: list[EventEnvelope] = []
        policy = ConsumerPolicy(consumer_group="test")
        for _ in range(3):
            await process_once(
                _event(), topic=TOPIC, handler=_collector(seen), store=store, policy=policy
            )
        assert len(seen) == 3, "event_id が異なれば別イベントとして処理される"


class TestRetryAndRecovery:
    async def test_transient_failure_is_retried(self) -> None:
        store = InMemoryIdempotencyStore()
        attempts: list[int] = []

        async def flaky(_: EventEnvelope) -> None:
            attempts.append(len(attempts))
            if len(attempts) < 3:
                raise ConnectionError("broker hiccup")

        async def no_sleep(_: float) -> None:
            return

        did_work = await process_once(
            _event(),
            topic=TOPIC,
            handler=flaky,
            store=store,
            policy=ConsumerPolicy(consumer_group="test", max_retries=3),
            sleep=no_sleep,
        )
        assert did_work is True
        assert len(attempts) == 3

    async def test_permanent_failure_unmarks_so_redelivery_can_retry(self) -> None:
        """リトライを使い切ったら冪等記録を取り消す（再配送で再試行できる状態に戻す）。"""
        store = InMemoryIdempotencyStore()

        async def always_fails(_: EventEnvelope) -> None:
            raise ConnectionError("broker down")

        async def no_sleep(_: float) -> None:
            return

        with pytest.raises(ConnectionError, match="broker down"):
            await process_once(
                _event(),
                topic=TOPIC,
                handler=always_fails,
                store=store,
                policy=ConsumerPolicy(consumer_group="test", max_retries=2),
                sleep=no_sleep,
            )
        assert len(store) == 0, "失敗したイベントは処理済みとして残してはいけない"

    async def test_retry_uses_exponential_backoff(self) -> None:
        store = InMemoryIdempotencyStore()
        delays: list[float] = []

        async def always_fails(_: EventEnvelope) -> None:
            raise ConnectionError("down")

        async def record(delay: float) -> None:
            delays.append(delay)

        with pytest.raises(ConnectionError):
            await process_once(
                _event(),
                topic=TOPIC,
                handler=always_fails,
                store=store,
                policy=ConsumerPolicy(consumer_group="test", max_retries=3, retry_base_delay_s=0.1),
                sleep=record,
            )
        assert delays == pytest.approx([0.1, 0.2, 0.4])


class TestOrderingAssumptions:
    def test_events_for_same_entity_share_partition_key(self) -> None:
        """同一実体のイベントは同じキー → 同じパーティション → 順序保証。"""
        keys = {_event().partition_key for _ in range(5)}
        assert len(keys) == 1

    def test_events_for_different_entities_have_different_keys(self) -> None:
        other = "https://sbp.example/site/siteA/asset/P-102"
        assert _event().partition_key != _event(subject=other).partition_key

    def test_event_ids_are_time_ordered_within_a_process(self) -> None:
        """UUIDv7 は時刻順序性を持つ（グローバル順序の保証ではない）。"""
        ids = [new_event_id() for _ in range(20)]
        stamps = [event_id_timestamp_ms(i) for i in ids]
        assert stamps == sorted(stamps)
