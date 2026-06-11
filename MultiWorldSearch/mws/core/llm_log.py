"""Append-only recorder for REAL LLM calls (IMPROVEMENT M16).

CLAUDE.md §5.1: cloud responses must be recorded when comparisons need
reproducibility. Live runs showed plan length varying across identically
seeded runs (7 vs 8 steps) with no artifact to explain it — every real ADK
call now appends one JSON line to ``runs/<RUN_ID>/llm_calls.jsonl``.

The file is created lazily on the FIRST record, so mock runs (which never
make real calls) produce no file and are byte-for-byte unchanged. Lives in
``core`` because agents must not depend on ``eval`` (CLAUDE.md §2.2 — eval is
output-only).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from mws.core.logging import get_logger

logger = get_logger(__name__)

#: Prompts are recorded in full up to this cap (full length is always kept in
#: ``prompt_chars``); responses are recorded in full — they are the artifact.
_PROMPT_CAP = 4000


class LLMCallRecorder:
    """Appends one JSONL line per real LLM call to a run artifact file."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._count = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def count(self) -> int:
        return self._count

    def record(
        self,
        *,
        agent: str,
        model: str,
        purpose: str,
        prompt: str,
        response: str,
        input_tokens: int,
        output_tokens: int,
        tokens_measured: bool,
        latency_ms: float,
    ) -> None:
        """Append one real-call record (creates the file on first use)."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "agent": agent,
            "model": model,
            "purpose": purpose,
            "prompt_chars": len(prompt),
            "prompt": prompt[:_PROMPT_CAP],
            "response": response,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tokens_measured": tokens_measured,
            "latency_ms": round(latency_ms, 1),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._count += 1
        logger.debug("LLM call recorded", purpose=purpose, n=self._count)
