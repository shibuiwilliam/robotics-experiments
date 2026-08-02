"""Gemini adapters (ER / ADK / Embedding) — the ONE cloud choke point (CLAUDE.md §0-1, §7).

Every Gemini call in Musubi lives behind the VCR here. Nothing outside this package imports
``google.genai`` / ``google.adk`` (and even here they are lazy-imported). Offline paths use
``FakeGeminiClient`` and committed cassettes.
"""

from __future__ import annotations

from clients.backends import (
    AnthropicBackend,
    FakeGeminiClient,
    GeminiBackend,
    select_chat_backend,
)
from clients.chat import ChatAdapter, make_chat_adapter
from clients.embedding import EmbeddingAdapter
from clients.er import ERAdapter, ERClient, ERPoint
from clients.guard import llm_imports_outside_clients
from clients.vcr import VCR, BudgetExceeded, MissingCassette, VcrMode

__all__ = [
    "VCR",
    "VcrMode",
    "MissingCassette",
    "BudgetExceeded",
    "ERAdapter",
    "ERClient",
    "ERPoint",
    "ChatAdapter",
    "make_chat_adapter",
    "EmbeddingAdapter",
    "FakeGeminiClient",
    "GeminiBackend",
    "AnthropicBackend",
    "select_chat_backend",
    "llm_imports_outside_clients",
]
