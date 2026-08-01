"""LLM VCR — the record/replay proxy that makes every Gemini call reproducible (PROJECT.md §7.3).

Modes: replay (cassettes only; unrecorded call -> MissingCassette), record (live on miss, then
save), passthrough (always live, no save). Key = hash(model_id, normalized_request); images are
content-hashed for dedup. Cassettes are committed JSON files (D-0003).
"""

from __future__ import annotations

from clients.vcr.exceptions import BudgetExceeded, MissingCassette
from clients.vcr.vcr import VCR, VcrMode

__all__ = ["VCR", "VcrMode", "MissingCassette", "BudgetExceeded"]
