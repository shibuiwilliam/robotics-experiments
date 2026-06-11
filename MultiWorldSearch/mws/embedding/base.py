"""Embedding protocol and result types."""

from __future__ import annotations

from typing import Protocol

import numpy as np
from pydantic import BaseModel

from mws.core.types import EmbeddingSpace


class EmbeddingResult(BaseModel):
    """Result from an embedder, always tagged with its space."""

    vector: list[float]
    space: EmbeddingSpace
    content_hash: str
    dims: int

    def to_numpy(self) -> np.ndarray:
        return np.array(self.vector, dtype=np.float32)


class Embedder(Protocol):
    """Protocol for embedding adapters (mock, local, cloud).

    Retrieval is ASYMMETRIC for models that support task instructions
    (IMPROVEMENT E1): documents and queries are formatted differently before
    embedding. ``format_document``/``format_query`` return the FINAL string
    that will be embedded — callers must hash/cache on that string, not the
    raw input. Symmetric embedders (mock, student) return the input unchanged,
    so their behavior is byte-for-byte identical to the pre-E1 code.
    """

    @property
    def space(self) -> EmbeddingSpace: ...

    @property
    def dims(self) -> int: ...

    def _content_hash(self, text: str) -> str:
        """Deterministic hash of the FINAL embedded string (cache key part)."""
        ...

    def format_document(self, text: str) -> str:
        """Return the final string embedded for the DOCUMENT (index) side."""
        ...

    def format_query(self, text: str) -> str:
        """Return the final string embedded for the QUERY (search) side."""
        ...

    def embed_text(self, text: str) -> EmbeddingResult:
        """Embed a text string as-is (no task formatting)."""
        ...

    def embed_document(self, text: str) -> EmbeddingResult:
        """Embed text for indexing (applies document formatting)."""
        ...

    def embed_query(self, text: str) -> EmbeddingResult:
        """Embed text for searching (applies query formatting)."""
        ...

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Embed a batch of texts in ONE request where the backend allows.

        Texts are embedded as-is — callers apply ``format_document`` /
        ``format_query`` first when role formatting is needed.
        """
        ...
