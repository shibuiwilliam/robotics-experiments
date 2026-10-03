"""structlog の設定。``print`` は禁止（CLAUDE.md コーディング規約）。

ログには ``event_id`` / ``correlation_id`` / ``entity`` を bind する。完了の定義に
「ログに相関 ID」が含まれるため、イベントを扱うコードは必ず :func:`bind_event` を通す。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

if TYPE_CHECKING:
    from sbp.core.events import EventEnvelope

__all__ = ["bind_event", "configure_logging", "get_logger"]


def configure_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    """プロセス全体のログ設定を行う。

    Args:
        level: ログレベル（``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR``）。
        json_output: 真なら JSON 行、偽なら人間可読のコンソール出力。
    """
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper()))
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """名前付きロガーを返す。

    Args:
        name: ロガー名（通常は ``__name__``）。
    """
    return structlog.get_logger(name)


def bind_event(
    logger: structlog.stdlib.BoundLogger, event: EventEnvelope, **extra: Any
) -> structlog.stdlib.BoundLogger:
    """イベントの相関 ID 群を bind したロガーを返す。

    Args:
        logger: 元のロガー。
        event: 対象イベント。
        **extra: 追加で bind する項目。
    """
    return logger.bind(
        event_id=str(event.event_id),
        correlation_id=_opt_str(event.correlation_id),
        causation_id=_opt_str(event.causation_id),
        entity=event.subject,
        event_type=event.type,
        **extra,
    )


def _opt_str(value: UUID | None) -> str | None:
    return None if value is None else str(value)
