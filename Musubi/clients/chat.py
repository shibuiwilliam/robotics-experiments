"""Chat adapter — provider-agnostic agent reasoning behind the VCR.

The agent-reasoning LLM is registry-selected (``llm.provider``: gemini | claude). Claude is the
primary engine (see IMPROVEMENT.md). Bench calls are stateless so replay stays valid (CLAUDE.md §8);
the adapter keys the VCR on (provider, model, prompt, schema, budget) and returns a structured dict.
Offline uses ``FakeGeminiClient`` regardless of provider.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from clients.backends import ChatBackend, FakeGeminiClient, select_chat_backend
from clients.vcr import VCR
from config import load_registry


def _schema_sig(schema: dict[str, Any] | None) -> str:
    if not schema:
        return "none"
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:16]


class ChatAdapter:
    """VCR-wrapped structured-generation client for the selected LLM provider."""

    def __init__(
        self,
        vcr: VCR | None = None,
        backend: ChatBackend | None = None,
        provider: str | None = None,
    ) -> None:
        self._vcr = vcr or VCR()
        reg = load_registry()
        self._provider = provider or reg.llm_provider()
        self._model = reg.agent_model(self._provider)
        # gemini carries a thinking budget; claude uses adaptive thinking (budget ignored).
        self._budget = int(reg.get("models.agent.thinking_budget.default", 0))
        self._backend = backend or select_chat_backend(self._provider)
        self.calls = 0  # number of generate() invocations (surfaced as llm_calls in run contexts)

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    def generate(self, prompt: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls += 1
        request: dict[str, Any] = {
            "provider": self._provider,
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


def make_chat_adapter(provider: str | None = None, *, offline_fake: bool = False) -> ChatAdapter:
    """Build a ChatAdapter for a provider. ``offline_fake=True`` forces the FakeGeminiClient double.

    The fake is the deterministic offline double (no network), so it runs behind a passthrough VCR —
    the fake IS the recording; the live-mode VCR gate is for real backends only.
    """
    if offline_fake:
        from clients.vcr import VCR, VcrMode

        return ChatAdapter(
            vcr=VCR(VcrMode.passthrough), backend=FakeGeminiClient(), provider=provider
        )
    return ChatAdapter(provider=provider)
