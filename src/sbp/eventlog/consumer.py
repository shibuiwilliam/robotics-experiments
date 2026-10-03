"""イベント受信（設計書 §6.1）。

Consumer は at-least-once。副作用は ``event_id`` で冪等化する（knowledge ルール）。
``causation_id`` / ``correlation_id`` は必ず引き継ぐ（ハンドラに渡すイベントがそれを持つ）。

順序は実体単位（パーティションキー＝主体実体 IRI）。グローバルな時刻順を前提にした処理を
書かない（不変条件 4）。

リトライが許されるのはこの層と ``llm.Provider`` の 2 箇所だけ（CLAUDE.md）。ここでは
ハンドラの失敗に対する有限回リトライと、超過時の隔離を実装する。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Self

from sbp.core.errors import SchemaValidationError
from sbp.core.events import EventEnvelope
from sbp.core.logging import bind_event, get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import TracebackType

    from sbp.core.config import Settings
    from sbp.eventlog.idempotency import IdempotencyStore

__all__ = ["ConsumerPolicy", "EventHandler", "KafkaEventConsumer", "process_once"]

log = get_logger(__name__)


class EventHandler(Protocol):
    """イベント 1 件を処理する副作用。

    引数は位置専用（``/``）。ハンドラ側が引数名を自由に付けられるようにするため、
    名前は契約に含めない。
    """

    async def __call__(self, event: EventEnvelope, /) -> None:
        """Args:
        event: 処理するイベント。
        """
        ...


@dataclass(frozen=True, slots=True)
class ConsumerPolicy:
    """consumer の挙動。

    Attributes:
        consumer_group: consumer グループ名。冪等化の単位でもある。
        max_retries: ハンドラ失敗時の再試行回数（この層に許されたリトライ）。
        retry_base_delay_s: 再試行の基準待ち時間（指数バックオフの基数）。
        auto_commit: オフセットの自動コミット。既定は偽（副作用の完了後に明示コミットする）。
    """

    consumer_group: str
    max_retries: int = 3
    retry_base_delay_s: float = 0.5
    auto_commit: bool = False


async def process_once(
    event: EventEnvelope,
    *,
    topic: str,
    handler: EventHandler,
    store: IdempotencyStore,
    policy: ConsumerPolicy,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> bool:
    """1 件のイベントを冪等に処理する。

    処理の順序は :mod:`sbp.eventlog.idempotency` の docstring のとおり。重複なら副作用を
    実行せずに ``False`` を返す。ハンドラが失敗したら有限回リトライし、それでも失敗したら
    冪等記録を取り消して例外を送出する（再配送で再試行できる状態に戻す）。

    Args:
        event: 処理するイベント。
        topic: 取得元トピック。
        handler: 副作用。
        store: 冪等ストア。
        policy: リトライ回数などの方針。
        sleep: 待機関数（テストで差し替えるため）。既定は :func:`asyncio.sleep`。

    Returns:
        副作用を実行したら ``True``、重複で飛ばしたら ``False``。

    Raises:
        Exception: リトライ上限を超えてハンドラが失敗した場合、最後の例外をそのまま送出する。
    """
    wait = sleep if sleep is not None else asyncio.sleep
    bound = bind_event(log, event, topic=topic, consumer_group=policy.consumer_group)

    is_new = await store.mark_processing(
        event_id=event.event_id,
        consumer_group=policy.consumer_group,
        topic=topic,
        subject=event.subject,
    )
    if not is_new:
        bound.debug("consumer.skipped_duplicate")
        return False

    last_error: BaseException | None = None
    for attempt in range(policy.max_retries + 1):
        try:
            await handler(event)
        except Exception as exc:
            last_error = exc
            if attempt < policy.max_retries:
                delay = policy.retry_base_delay_s * (2**attempt)
                bound.warning(
                    "consumer.handler_failed_retrying",
                    attempt=attempt + 1,
                    max_retries=policy.max_retries,
                    delay_s=delay,
                    error=str(exc),
                )
                await wait(delay)
                continue
            break
        else:
            bound.debug("consumer.handled", attempts=attempt + 1)
            return True

    # リトライを使い切った。冪等記録を取り消して再配送に委ねる。
    await store.unmark(event_id=event.event_id, consumer_group=policy.consumer_group)
    bound.error("consumer.handler_failed", error=str(last_error))
    assert last_error is not None
    raise last_error


class KafkaEventConsumer:
    """Redpanda（Kafka API）からの consumer。

    SIGTERM で graceful shutdown し、オフセットをコミットしてから終了する（infra-testing ルール）。
    停止の指示は :meth:`request_stop` で行い、``run`` のループが次の取得境界で抜ける。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        topics: tuple[str, ...],
        policy: ConsumerPolicy,
        store: IdempotencyStore,
        handler: EventHandler,
    ) -> None:
        """Args:
        settings: 接続設定。
        topics: 購読するトピック。
        policy: consumer の方針。
        store: 冪等ストア。
        handler: 副作用。
        """
        self._settings = settings
        self._topics = topics
        self._policy = policy
        self._store = store
        self._handler = handler
        self._consumer: object | None = None
        self._stopping = asyncio.Event()

    async def __aenter__(self) -> Self:
        """consumer を起動する。"""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """オフセットをコミットして停止する。"""
        await self.stop()

    async def start(self) -> None:
        """ブローカーに接続して購読を始める。"""
        from aiokafka import AIOKafkaConsumer  # noqa: PLC0415

        consumer = AIOKafkaConsumer(
            *self._topics,
            bootstrap_servers=self._settings.kafka_bootstrap,
            group_id=f"{self._settings.consumer_group_prefix}.{self._policy.consumer_group}",
            enable_auto_commit=self._policy.auto_commit,
            auto_offset_reset="earliest",
        )
        await consumer.start()
        self._consumer = consumer
        log.info(
            "consumer.started",
            topics=list(self._topics),
            consumer_group=self._policy.consumer_group,
        )

    async def stop(self) -> None:
        """オフセットをコミットしてから接続を閉じる。"""
        if self._consumer is None:
            return
        consumer = self._consumer
        self._consumer = None
        if not self._policy.auto_commit:
            await consumer.commit()  # type: ignore[attr-defined]
        await consumer.stop()  # type: ignore[attr-defined]
        log.info("consumer.stopped", consumer_group=self._policy.consumer_group)

    def request_stop(self) -> None:
        """停止を要求する（SIGTERM ハンドラから呼ぶ）。"""
        self._stopping.set()

    async def run(self, *, max_events: int | None = None) -> int:
        """イベントを処理し続ける。

        Args:
            max_events: この件数を処理したら戻る（テスト・バッチ用）。``None`` なら
                :meth:`request_stop` が呼ばれるまで続ける。

        Returns:
            副作用を実行した件数（重複で飛ばした分は含まない）。

        Raises:
            RuntimeError: ``start()`` を呼んでいない場合。
        """
        if self._consumer is None:
            raise RuntimeError("consumer is not started; use `async with` or call start()")
        handled = 0
        while not self._stopping.is_set():
            batch = await self._consumer.getmany(timeout_ms=500)  # type: ignore[attr-defined]
            if not batch:
                if max_events is not None:
                    break
                continue
            for records in batch.values():
                for record in records:
                    try:
                        event = EventEnvelope.model_validate_json(record.value)
                    except Exception as exc:
                        log.error(
                            "consumer.undecodable_payload",
                            topic=record.topic,
                            offset=record.offset,
                            error=str(exc),
                        )
                        raise SchemaValidationError(
                            f"undecodable payload at {record.topic}@{record.offset}"
                        ) from exc
                    did_work = await process_once(
                        event,
                        topic=record.topic,
                        handler=self._handler,
                        store=self._store,
                        policy=self._policy,
                    )
                    if did_work:
                        handled += 1
                    if max_events is not None and handled >= max_events:
                        if not self._policy.auto_commit:
                            await self._consumer.commit()  # type: ignore[attr-defined]
                        return handled
            if not self._policy.auto_commit:
                await self._consumer.commit()  # type: ignore[attr-defined]
        return handled
