"""Tests for vector store."""

import numpy as np

from mws.core.types import EmbeddingSpace
from mws.storage.vector import VectorStore


def test_add_and_search() -> None:
    vs = VectorStore(embedding_space=EmbeddingSpace.MOCK_128, dims=4)
    vs.add("a", np.array([1.0, 0.0, 0.0, 0.0]))
    vs.add("b", np.array([0.0, 1.0, 0.0, 0.0]))
    vs.add("c", np.array([0.9, 0.1, 0.0, 0.0]))

    results = vs.search(np.array([1.0, 0.0, 0.0, 0.0]), top_k=2)
    assert len(results) == 2
    # "a" and "c" should be most similar to [1,0,0,0]
    ids = [r[0] for r in results]
    assert "a" in ids
    assert "c" in ids


def test_empty_search() -> None:
    vs = VectorStore(embedding_space=EmbeddingSpace.MOCK_128, dims=4)
    assert vs.search(np.array([1.0, 0.0, 0.0, 0.0])) == []


def test_remove() -> None:
    vs = VectorStore(embedding_space=EmbeddingSpace.MOCK_128, dims=4)
    vs.add("a", np.array([1.0, 0.0, 0.0, 0.0]))
    assert vs.size == 1
    vs.remove("a")
    assert vs.size == 0
