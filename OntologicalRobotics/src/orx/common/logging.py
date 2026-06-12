"""構造化ログ（structlog, JSON lines）→ data/runs/<run_id>/log.jsonl。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog


def make_run_logger(run_dir: Path, **initial: Any) -> structlog.stdlib.BoundLogger:
    """run ディレクトリ配下に JSONL を書くロガーを生成する。

    プロセスグローバル設定を汚さないよう wrap_logger を使う。
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    log_file = (run_dir / "log.jsonl").open("a", encoding="utf-8")
    logger = structlog.wrap_logger(
        structlog.WriteLogger(log_file),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
    )
    return logger.bind(**initial)
