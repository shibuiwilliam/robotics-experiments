"""JSONL audit logger for scenario runs.

Every query, action, and write-back is appended to runs/<RUN_ID>/audit.jsonl.
Lines contain: timestamp, run_id, actor, operation, target atom/entity ID,
indices used, projection type, provenance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mws.core.logging import get_logger

logger = get_logger(__name__)


class AuditLogger:
    """Append-only JSONL audit logger for a scenario run."""

    def __init__(self, run_dir: Path, run_id: str) -> None:
        self._run_dir = run_dir
        self._run_id = run_id
        self._path = run_dir / "audit.jsonl"
        self._entries: list[dict[str, Any]] = []
        self._total_logged = 0  # cumulative; survives flush() (IMPROVEMENT M6)
        run_dir.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        phase: str,
        actor: str,
        operation: str,
        *,
        target_id: str = "",
        entity_id: str = "",
        indices: list[str] | None = None,
        projection: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append an audit entry."""
        entry: dict[str, Any] = {
            "run_id": self._run_id,
            "phase": phase,
            "actor": actor,
            "operation": operation,
        }
        if target_id:
            entry["target_id"] = target_id
        if entity_id:
            entry["entity_id"] = entity_id
        if indices:
            entry["indices"] = indices
        if projection:
            entry["projection"] = projection
        if details:
            entry["details"] = details
        self._entries.append(entry)
        self._total_logged += 1

    def flush(self) -> None:
        """Write all buffered entries to the JSONL file."""
        with open(self._path, "a") as f:
            for entry in self._entries:
                f.write(json.dumps(entry, default=str) + "\n")
        logger.debug("Flushed audit log", n_entries=len(self._entries), path=str(self._path))
        self._entries.clear()

    @property
    def entries(self) -> list[dict[str, Any]]:
        """Get all buffered entries (for in-process inspection)."""
        return list(self._entries)

    @property
    def entry_count(self) -> int:
        """Total entries logged this run (cumulative — NOT reset by flush()).

        Scenarios read this after flushing for their final summary line; a
        buffer-length implementation made that always print 0 (IMPROVEMENT M6).
        """
        return self._total_logged
