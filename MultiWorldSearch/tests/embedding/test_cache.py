"""Tests for embedding cache."""

from mws.core.types import EmbeddingSpace
from mws.embedding.base import EmbeddingResult
from mws.embedding.cache import EmbeddingCache


def test_cache_hit() -> None:
    cache = EmbeddingCache()
    result = EmbeddingResult(
        vector=[0.1] * 128,
        space=EmbeddingSpace.MOCK_128,
        content_hash="abc",
        dims=128,
    )
    cache.put(result)
    hit = cache.get("abc", EmbeddingSpace.MOCK_128)
    assert hit is not None
    assert hit.vector == result.vector


def test_cache_miss_different_space() -> None:
    cache = EmbeddingCache()
    result = EmbeddingResult(
        vector=[0.1] * 128,
        space=EmbeddingSpace.MOCK_128,
        content_hash="abc",
        dims=128,
    )
    cache.put(result)
    # Same content hash but different space -> miss
    miss = cache.get("abc", EmbeddingSpace.GEMINI_768)
    assert miss is None


def test_hit_rate() -> None:
    cache = EmbeddingCache()
    result = EmbeddingResult(
        vector=[0.1] * 128, space=EmbeddingSpace.MOCK_128, content_hash="x", dims=128
    )
    cache.put(result)
    cache.get("x", EmbeddingSpace.MOCK_128)  # hit
    cache.get("y", EmbeddingSpace.MOCK_128)  # miss
    assert cache.hit_rate == 0.5
