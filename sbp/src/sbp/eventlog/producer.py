"""イベント送信（設計書 §6.1）。

Producer は :class:`~sbp.core.events.EventEnvelope` 以外を送れない（knowledge ルール）。
送信前に ``schemas/`` の JSON Schema で検証し、パーティションキーは主体実体 IRI にする
（実体単位の順序保証。不変条件 4）。

実ブローカーへの送信は :class:`KafkaEventProducer`、テストでは
:class:`InMemoryEventProducer` を使う。
どちらも :class:`EventProducer` Protocol を満たす（外部 I/O は Protocol でモックする）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

from sbp.core.events import EventEnvelope
from sbp.core.logging import bind_event, get_logger
from sbp.eventlog.schemas import validate_event

if TYPE_CHECKING:
    from types import TracebackType

    from sbp.core.config import Settings

__all__ = ["EventProducer", "InMemoryEventProducer", "KafkaEventProducer"]

log = get_logger(__name__)


def _identity(value: bytes) -> bytes:
    """恒等関数。ペイロードは呼び出し側で既にバイト列にしてある。"""
    return value


@runtime_checkable
class EventProducer(Protocol):
    """イベント送信の抽象。"""

    async def send(self, topic: str, event: EventEnvelope) -> None:
        """イベントを 1 件送る。

        Args:
            topic: 送信先トピック（``<site>.<layer>.<class>``）。
            event: 送信するイベント。
        """
        ...

    async def flush(self) -> None:
        """未送信のバッファを送り切る。"""
        ...


class InMemoryEventProducer:
    """テスト用の producer。送ったイベントを保持するだけで外部 I/O を行わない。

    スキーマ検証は実装と同じく行う（検証を通さない抜け道をテスト側に作らないため）。
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, EventEnvelope]] = []
        """(トピック, イベント) の送信順リスト。"""

    async def send(self, topic: str, event: EventEnvelope) -> None:
        """イベントを記録する。

        Args:
            topic: 送信先トピック。
            event: 送信するイベント。
        """
        validate_event(event)
        self.sent.append((topic, event))

    async def flush(self) -> None:
        """何もしない（バッファを持たない）。"""
        return

    def events_for(self, topic: str) -> list[EventEnvelope]:
        """指定トピックに送られたイベントを返す。

        Args:
            topic: 対象トピック。
        """
        return [e for t, e in self.sent if t == topic]


class KafkaEventProducer:
    """Redpanda（Kafka API）への producer。

    ``aiokafka`` を用い、パーティションキーに主体実体 IRI を使う。``acks="all"`` と
    冪等 producer を有効にして、再送時の重複を抑える（最終的な冪等性は consumer 側の
    ``event_id`` 判定で担保する。不変条件 4）。
    """

    def __init__(self, settings: Settings) -> None:
        """Args:
        settings: 接続設定（``kafka_bootstrap`` を使う）。
        """
        self._settings = settings
        self._producer: object | None = None

    async def __aenter__(self) -> Self:
        """producer を起動する。"""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """producer を停止する（バッファを送り切ってから閉じる）。"""
        await self.stop()

    async def start(self) -> None:
        """ブローカーに接続する。"""
        from aiokafka import AIOKafkaProducer  # noqa: PLC0415

        producer = AIOKafkaProducer(
            bootstrap_servers=self._settings.kafka_bootstrap,
            acks="all",
            enable_idempotence=True,
            # イベントは JSON。シリアライズは validate_event が返すバイト列を使うため
            # ここでは恒等関数にする。
            value_serializer=_identity,
            key_serializer=_identity,
        )
        await producer.start()
        self._producer = producer
        log.info("producer.started", bootstrap=self._settings.kafka_bootstrap)

    async def stop(self) -> None:
        """バッファを送り切って接続を閉じる。"""
        if self._producer is None:
            return
        producer = self._producer
        self._producer = None
        await producer.stop()  # type: ignore[attr-defined]
        log.info("producer.stopped")

    async def send(self, topic: str, event: EventEnvelope) -> None:
        """イベントを 1 件送る（スキーマ検証を通してから）。

        Args:
            topic: 送信先トピック。
            event: 送信するイベント。

        Raises:
            RuntimeError: ``start()`` を呼んでいない場合。
            SchemaValidationError: 生成済みスキーマに適合しない場合。
        """
        if self._producer is None:
            raise RuntimeError("producer is not started; use `async with` or call start()")
        payload = validate_event(event)
        await self._producer.send_and_wait(  # type: ignore[attr-defined]
            topic, value=payload, key=event.partition_key
        )
        bind_event(log, event).debug("producer.sent", topic=topic)

    async def flush(self) -> None:
        """未送信のバッファを送り切る。"""
        if self._producer is not None:
            await self._producer.flush()  # type: ignore[attr-defined]
