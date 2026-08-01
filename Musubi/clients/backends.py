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
