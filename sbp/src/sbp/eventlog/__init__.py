"""イベントログ（記録源）。Redpanda producer・consumer、スキーマ検証、冪等性。

責務: イベントの送受信、``schemas/*.json`` による検証、``event_id`` による冪等化、
順序（実体単位）。
非責務: 意味の確定、永続化先の選択、推論。

この層とともに ``llm.Provider`` だけがリトライを行う（CLAUDE.md）。
"""

from __future__ import annotations

from sbp.eventlog.consumer import ConsumerPolicy, EventHandler, KafkaEventConsumer, process_once
from sbp.eventlog.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
    PostgresIdempotencyStore,
)
from sbp.eventlog.producer import EventProducer, InMemoryEventProducer, KafkaEventProducer
from sbp.eventlog.schemas import validate_event

__all__ = [
    "ConsumerPolicy",
    "EventHandler",
    "EventProducer",
    "IdempotencyStore",
    "InMemoryEventProducer",
    "InMemoryIdempotencyStore",
    "KafkaEventConsumer",
    "KafkaEventProducer",
    "PostgresIdempotencyStore",
    "process_once",
    "validate_event",
]
