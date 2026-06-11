"""Content-hash embedding cache — never embed the same content twice."""

from __future__ import annotations

from mws.core.logging import get_logger
from mws.core.types import EmbeddingSpace
from mws.embedding.base import EmbeddingResult

logger = get_logger(__name__)


class EmbeddingCache:
    """In-memory content-hash cache for embeddings.

    Cache key = (content_hash, embedding_space). This ensures:
    - Same content with same model -> cache hit
    - Same content with different model -> cache miss (correct)
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, EmbeddingSpace], EmbeddingResult] = {}
        self._hits = 0
        self._misses = 0

    def get(self, content_hash: str, space: EmbeddingSpace) -> EmbeddingResult | None:
        """Look up a cached embedding."""
        result = self._cache.get((content_hash, space))
        if result is not None:
            self._hits += 1
        else:
            self._misses += 1
        return result

    def put(self, result: EmbeddingResult) -> None:
        """Cache an embedding result."""
        self._cache[(result.content_hash, result.space)] = result

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def size(self) -> int:
        return len(self._cache)
