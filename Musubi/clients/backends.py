"""Provider backends: the raw Gemini calls (lazy-imported) and the offline FakeGeminiClient.

A backend returns JSON-serializable data; the adapters wrap the VCR around it and parse into domain
objects. ``GeminiBackend`` lazy-imports ``google.genai`` / ``google.adk`` so the offline core never
loads them. ``FakeGeminiClient`` is a schema-valid deterministic double for offline tests (Prime
Directive: the system must build+prove offline with no keys).
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

import numpy as np


class ERBackend(Protocol):
    def detect_points(
        self, image: np.ndarray, query: str, model_id: str, thinking_budget: int
    ) -> list[dict[str, Any]]: ...


class ChatBackend(Protocol):
    def generate(
        self, prompt: str, model_id: str, schema: dict[str, Any] | None, thinking_budget: int
    ) -> dict[str, Any]: ...


class EmbeddingBackend(Protocol):
    def embed(self, content: str, model_id: str, dim: int) -> list[float]: ...


# --------------------------------------------------------------------------- Gemini (live)
class GeminiBackend:
    """Real Gemini backend. Imports the SDKs lazily so offline runs never touch them."""

    def _genai(self) -> Any:  # pragma: no cover - requires network + key
        from google import genai  # lazy: only on live record/passthrough

        return genai

    def detect_points(
        self, image: np.ndarray, query: str, model_id: str, thinking_budget: int
    ) -> list[dict[str, Any]]:  # pragma: no cover - live only
        raise NotImplementedError(
            "GeminiBackend.detect_points requires a key + network; record cassettes via `make test-live`."
        )

    def generate(
        self, prompt: str, model_id: str, schema: dict[str, Any] | None, thinking_budget: int
    ) -> dict[str, Any]:  # pragma: no cover - live only
        raise NotImplementedError(
            "GeminiBackend.generate requires a key + network; record cassettes via `make test-live`."
        )

    def embed(self, content: str, model_id: str, dim: int) -> list[float]:  # pragma: no cover
        raise NotImplementedError(
            "GeminiBackend.embed requires a key + network; record cassettes via `make test-live`."
        )


# --------------------------------------------------------------------------- Anthropic / Claude (live)
class AnthropicBackend:
    """Real Claude agent backend (ChatBackend). Lazy-imports ``anthropic`` so offline never loads it.

    Uses adaptive thinking + effort and structured outputs (``output_config.format``) so the response
    is schema-valid JSON — the same contract the planner consumes. Model id + effort + max_tokens come
    from ``config/registry.yaml`` (models.claude). Claude does agent reasoning only; ER/embedding stay
    Gemini (Anthropic offers neither).
    """

    _SYSTEM = (
        "You are Musubi's planning agent. Return ONLY the JSON your schema requires — no prose. "
        "Respect the ontology vocabulary and the reversibility/norm gates."
    )

    def detect_points(
        self, image: np.ndarray, query: str, model_id: str, thinking_budget: int
    ) -> list[dict[str, Any]]:  # pragma: no cover - Claude has no ER; use Gemini ER
        raise NotImplementedError("AnthropicBackend does not do ER pointing; ER stays on Gemini.")

    def embed(self, content: str, model_id: str, dim: int) -> list[float]:  # pragma: no cover
        raise NotImplementedError(
            "AnthropicBackend does not do embeddings; embeddings stay on Gemini."
        )

    def generate(
        self, prompt: str, model_id: str, schema: dict[str, Any] | None, thinking_budget: int
    ) -> dict[str, Any]:  # pragma: no cover - requires ANTHROPIC_API_KEY + network
        import json as _json

        import anthropic  # lazy: only on live record/passthrough

        from config import load_registry

        reg = load_registry()
        effort = str(reg.get("models.claude.effort", "high"))
        max_tokens = int(reg.get("models.claude.max_tokens", 16000))

        output_config: dict[str, Any] = {"effort": effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}

        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        response = client.messages.create(
            model=model_id,  # e.g. claude-opus-4-8 (registry)
            max_tokens=max_tokens,
            system=self._SYSTEM,
            thinking={"type": "adaptive"},  # budget_tokens is removed on 4.8 (would 400)
            output_config=output_config,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        result: dict[str, Any] = _json.loads(text) if text else {}
        return result


def select_chat_backend(provider: str) -> ChatBackend:
    """Return the live ChatBackend for a provider (claude → Anthropic, else Gemini).

    Offline paths never call this backend (replay serves cassettes / a FakeGeminiClient is injected);
    it is the live backend used only in record/passthrough.
    """
    if provider == "claude":
        return AnthropicBackend()
    return GeminiBackend()


# --------------------------------------------------------------------------- Fake (offline)
def _seeded_vector(content: str, dim: int) -> list[float]:
    seed = int.from_bytes(hashlib.sha256(content.encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim)
    norm = float(np.linalg.norm(vec)) or 1.0
    return [float(v / norm) for v in vec]  # unit-normalized (matches MRL truncation-normalization)


def minimal_instance(schema: dict[str, Any]) -> Any:
    """Construct a minimal value satisfying a (subset of) JSON Schema — for schema-valid fakes."""
    if "$ref" in schema and "$defs" in schema:
        ref = schema["$ref"].split("/")[-1]
        return minimal_instance({**schema["$defs"][ref], "$defs": schema["$defs"]})
    stype = schema.get("type")
    if stype == "object" or "properties" in schema:
        defs = schema.get("$defs", {})
        out: dict[str, Any] = {}
        for name in schema.get("required", []):
            prop = schema.get("properties", {}).get(name, {})
            out[name] = minimal_instance({**prop, "$defs": defs} if defs else prop)
        return out
    if stype == "array":
        return []
    if stype == "boolean":
        return False
    if stype == "integer":
        return 0
    if stype in ("number",):
        return 0.0
    if "enum" in schema:
        return schema["enum"][0]
    return ""


class FakeGeminiClient:
    """Deterministic, schema-valid offline double implementing all three backends."""

    def __init__(self, canned_points: list[dict[str, Any]] | None = None) -> None:
        # default: three center-ish points (schema-valid; not world-accurate — perception tests
        # use a world-aware fake for accuracy).
        self._points = canned_points or [
            {"y": 400.0, "x": 400.0, "label": "pallet", "confidence": 0.8},
            {"y": 500.0, "x": 520.0, "label": "pallet", "confidence": 0.75},
        ]
        self._canned_generate: dict[str, dict[str, Any]] = {}

    def register_generate(self, intent: str, response: dict[str, Any]) -> None:
        """Register a canned structured response for a given intent (used by GeminiPlanner tests)."""
        self._canned_generate[intent] = response

    def detect_points(
        self, image: np.ndarray, query: str, model_id: str, thinking_budget: int
    ) -> list[dict[str, Any]]:
        return [dict(p) for p in self._points]

    def generate(
        self, prompt: str, model_id: str, schema: dict[str, Any] | None, thinking_budget: int
    ) -> dict[str, Any]:
        for intent, response in self._canned_generate.items():
            if intent in prompt:
                return response
        return minimal_instance(schema) if schema else {}

    def embed(self, content: str, model_id: str, dim: int) -> list[float]:
        return _seeded_vector(content, dim)
