"""Structured logging setup for MWS. No print() — use this logger.

Logs go to STDERR so that command stdout stays machine-readable (e.g. the
`mws eval multi-seed` JSON output can be piped into json.load — IMPROVEMENT R12).
"""

from __future__ import annotations

import sys

import structlog


def setup_logging(*, json_output: bool = False, level: str = "INFO") -> None:
    """Configure structlog for MWS.

    Args:
        json_output: If True, output JSON lines. If False, console-friendly output.
        level: Log level string.
    """
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger for the given module name."""
    return structlog.get_logger(name)
