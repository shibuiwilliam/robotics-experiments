"""ER adapter — Gemini Robotics ER behind the VCR.

Defines the perception-facing ER interface (``ERClient`` / ``ERPoint``) — perception depends on
clients (PROJECT.md §6.2 PER→CLI). The adapter hashes the image (content-dedup), keys the VCR on
(model, query, image-hash, thinking-budget), and parses the response into ``ERPoint`` objects.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from clients.backends import ERBackend, FakeGeminiClient
from clients.vcr import VCR
from config import load_registry


@dataclass(frozen=True)
class ERPoint:
    """A single ER detection: a normalized [y, x] point (0..1000), optional label + confidence."""

    y: float
    x: float
    label: str | None = None
    confidence: float = 0.8


class ERClient(Protocol):
    """The perception-facing slice of the ER adapter (behind the VCR)."""

    def detect_points(self, image: np.ndarray, query: str) -> list[ERPoint]: ...


def _image_sha(image: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()[:20]


class ERAdapter:
    """VCR-wrapped ER client. Offline it replays cassettes; live (record) it calls Gemini ER."""

    def __init__(self, vcr: VCR | None = None, backend: ERBackend | None = None) -> None:
        self._vcr = vcr or VCR()
        self._backend = backend or FakeGeminiClient()
        reg = load_registry()
        self._model = reg.model_id("er")
        self._budget = int(reg.require("models.er.thinking_budget.detection"))

    def detect_points(self, image: np.ndarray, query: str) -> list[ERPoint]:
        request: dict[str, Any] = {
            "image_sha": _image_sha(image),
            "query": query,
            "thinking_budget": self._budget,
        }
        raw = self._vcr.interact(
            self._model,
            request,
            live_fn=lambda: self._backend.detect_points(image, query, self._model, self._budget),
        )
        return [
            ERPoint(
                y=float(p["y"]),
                x=float(p["x"]),
                label=p.get("label"),
                confidence=float(p.get("confidence", 0.8)),
            )
            for p in raw
        ]
