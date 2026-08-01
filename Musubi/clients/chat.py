"""ADK/chat adapter — Gemini agent reasoning behind the VCR.

Bench calls are stateless (no Interactions API) so replay stays valid (CLAUDE.md §8). The adapter
keys the VCR on (model, prompt, schema, thinking-budget) and returns a structured dict.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from clients.backends import ChatBackend, FakeGeminiClient
from clients.vcr import VCR
from config import load_registry


def _schema_sig(schema: dict[str, Any] | None) -> str:
    if not schema:
        return "none"
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:16]


class ChatAdapter:
    """VCR-wrapped structured-generation client (ADK/Gemini). Offline uses FakeGeminiClient."""

    def __init__(self, vcr: VCR | None = None, backend: ChatBackend | None = None) -> None:
        self._vcr = vcr or VCR()
        self._backend = backend or FakeGeminiClient()
        reg = load_registry()
        self._model = reg.model_id("agent")
        self._budget = int(reg.require("models.agent.thinking_budget.default"))

    def generate(self, prompt: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {
            "prompt": prompt,
            "schema": _schema_sig(schema),
            "thinking_budget": self._budget,
        }
        result: dict[str, Any] = self._vcr.interact(
            self._model,
            request,
            live_fn=lambda: self._backend.generate(prompt, self._model, schema, self._budget),
        )
        return result
