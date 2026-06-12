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
        import threading

        self._path = Path(path)
        self._count = 0
        # Concurrent step execution (Backlog B) appends from worker threads.
        self._lock = threading.Lock()

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
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._count += 1
        logger.debug("LLM call recorded", purpose=purpose, n=self._count)


class ReplayMismatchError(RuntimeError):
    """The next recorded call does not match the requested (agent, purpose)."""


class ReplayExhaustedError(RuntimeError):
    """More calls requested than were recorded."""


class LLMReplaySource:
    """Deterministic replay of a recorded llm_calls.jsonl (CLAUDE.md §5.1).

    Each (agent, purpose) consumes the FIRST unconsumed matching record —
    order-tolerant because parallel step execution (Backlog B) records lines
    in completion order, which can permute across runs, while (agent, purpose)
    pairs stay unique and deterministic. Any miss or exhaustion raises loudly;
    silently falling through to a real cloud call would corrupt both the
    experiment and the reconciliation audit.

    Replayed calls make NO cloud round-trips: callers must not log the real-
    call marker, must not re-record, and replayed runs are reproduction
    artifacts — they are NOT valid inputs to ``mws eval reconcile``.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        lines = [ln for ln in self._path.read_text().splitlines() if ln.strip()]
        self._records: list[dict] = [json.loads(ln) for ln in lines]
        self._consumed: list[bool] = [False] * len(self._records)
        logger.info("LLM replay source loaded", path=str(path), n_records=len(self._records))

    @property
    def remaining(self) -> int:
        return self._consumed.count(False)

    def next(self, *, agent: str, purpose: str) -> dict:
        """Consume and return the first unconsumed record for (agent, purpose)."""
        if self.remaining == 0:
            raise ReplayExhaustedError(
                f"replay exhausted after {len(self._records)} records; "
                f"requested ({agent!r}, {purpose!r})"
            )
        for i, record in enumerate(self._records):
            if self._consumed[i]:
                continue
            if record.get("agent") == agent and record.get("purpose") == purpose:
                self._consumed[i] = True
                return record
        unconsumed = [
            (r.get("agent"), r.get("purpose"))
            for i, r in enumerate(self._records)
            if not self._consumed[i]
        ]
        raise ReplayMismatchError(
            f"no recorded call matches ({agent!r}, {purpose!r}); remaining: {unconsumed}"
        )
