"""Deterministic mock embedder — seed-based, no cloud calls."""

from __future__ import annotations

import hashlib

import numpy as np
import xxhash

from mws.core.types import EmbeddingSpace
from mws.embedding.base import EmbeddingResult


class MockEmbedder:
    #: Latency-decomposition bucket (CLAUDE.md §10). Local/deterministic.
    latency_bucket = "local_embed"

    """Deterministic mock embedder that produces reproducible vectors from content hash.

    Uses a seeded PRNG keyed on content hash to generate vectors.
    This ensures: same text -> same vector, different text -> different vector,
    all deterministic with no network calls.
    """

    def __init__(
        self,
        space: EmbeddingSpace = EmbeddingSpace.MOCK_128,
        dims: int = 128,
        seed: int = 0,
    ) -> None:
        self._space = space
        self._dims = dims
        self._seed = seed

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    @property
    def dims(self) -> int:
        return self._dims

    def _content_hash(self, text: str) -> str:
        """Produce a deterministic hash for caching."""
        return xxhash.xxh64(text.encode()).hexdigest()

    def _generate_vector(self, text: str) -> np.ndarray:
        """Generate a deterministic vector from text content."""
        # Use content hash as seed for numpy PRNG
        h = hashlib.sha256(text.encode()).digest()
        content_seed = int.from_bytes(h[:8], "big") ^ self._seed
        rng = np.random.default_rng(content_seed & 0xFFFFFFFF)
        vec = rng.standard_normal(self._dims).astype(np.float32)
        # Normalize to unit sphere
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    def format_document(self, text: str) -> str:
        """Symmetric embedder: no task formatting (identity)."""
        return text

    def format_query(self, text: str) -> str:
        """Symmetric embedder: no task formatting (identity)."""
        return text

    def embed_text(self, text: str) -> EmbeddingResult:
        """Embed a single text, deterministically."""
        vec = self._generate_vector(text)
        return EmbeddingResult(
            vector=vec.tolist(),
            space=self._space,
            content_hash=self._content_hash(text),
            dims=self._dims,
        )

    def embed_document(self, text: str) -> EmbeddingResult:
        return self.embed_text(self.format_document(text))

    def embed_query(self, text: str) -> EmbeddingResult:
        return self.embed_text(self.format_query(text))

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Embed a batch of texts."""
        return [self.embed_text(t) for t in texts]
