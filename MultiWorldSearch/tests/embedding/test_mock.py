"""Tests for mock embedder determinism."""

from mws.core.types import EmbeddingSpace
from mws.embedding.mock import MockEmbedder


def test_deterministic_same_input() -> None:
    emb = MockEmbedder(seed=42)
    r1 = emb.embed_text("motor temperature anomaly")
    r2 = emb.embed_text("motor temperature anomaly")
    assert r1.vector == r2.vector
    assert r1.content_hash == r2.content_hash


def test_different_input_different_vector() -> None:
    emb = MockEmbedder(seed=42)
    r1 = emb.embed_text("motor temperature")
    r2 = emb.embed_text("conveyor belt jam")
    assert r1.vector != r2.vector


def test_different_seed_different_vector() -> None:
    e1 = MockEmbedder(seed=0)
    e2 = MockEmbedder(seed=99)
    r1 = e1.embed_text("hello")
    r2 = e2.embed_text("hello")
    assert r1.vector != r2.vector


def test_space_tagging() -> None:
    emb = MockEmbedder(space=EmbeddingSpace.MOCK_768, dims=768)
    result = emb.embed_text("test")
    assert result.space == EmbeddingSpace.MOCK_768
    assert result.dims == 768
    assert len(result.vector) == 768


def test_batch_embed() -> None:
    emb = MockEmbedder(seed=0)
    results = emb.embed_batch(["a", "b", "c"])
    assert len(results) == 3
    assert results[0].vector != results[1].vector
