"""Backend-aware vector-search latency bucket (IMPROVEMENT G6).

An ES query is an HTTP round-trip, not local ANN; it must be timed under a
distinct bucket so the CLAUDE.md §10 latency decomposition isn't misread.
"""

from __future__ import annotations

from mws.storage.vector import (
    ElasticsearchVectorStore,
    LanceDBVectorStore,
    VectorStore,
)


def test_memory_store_bucket_is_local_ann() -> None:
    assert VectorStore.latency_bucket == "local_ann"


def test_lancedb_store_bucket_is_local_ann() -> None:
    assert LanceDBVectorStore.latency_bucket == "local_ann"


def test_elasticsearch_store_bucket_is_es_search() -> None:
    # Class attribute — no instantiation / running cluster required.
    assert ElasticsearchVectorStore.latency_bucket == "es_search"


def test_engine_resolves_bucket_from_store_not_hardcoded() -> None:
    """engine.search reads the store's `latency_bucket` (G6), not a literal.

    Guards against regressing to the hardcoded "local_ann" that mislabeled ES
    round-trips. We assert the source uses the dynamic lookup.
    """
    import inspect

    from mws.retrieval import engine as engine_mod

    src = inspect.getsource(engine_mod.RetrievalEngine.search)
    assert 'getattr(self.stores.vector, "latency_bucket"' in src
    assert "self._latency.track(vbucket)" in src
