"""Elasticsearch vector backend — integration tests.

Marked @pytest.mark.elasticsearch and DESELECTED by default (the suite stays
offline and deterministic). To run them:

    make es-up                       # docker compose up -d --wait elasticsearch
    uv sync --extra es
    uv run pytest -m elasticsearch

Each test skips (not fails) if Elasticsearch is unreachable or the client
extra is missing, so a developer without Docker is never blocked.
"""

from __future__ import annotations

import os
import uuid

import numpy as np
import pytest

from mws.core.types import EmbeddingSpace

pytestmark = pytest.mark.elasticsearch

_URL = os.environ.get("MWS_ELASTICSEARCH_URL", "http://localhost:9200")


def _store(dims: int = 8):
    """Build an ES vector store on a unique index, skipping if unavailable."""
    try:
        from elasticsearch import Elasticsearch
    except ImportError:
        pytest.skip("elasticsearch client not installed (uv sync --extra es)")
    try:
        if not Elasticsearch(_URL).ping():
            pytest.skip(f"Elasticsearch not reachable at {_URL} (make es-up)")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Elasticsearch not reachable: {exc}")

    from mws.storage.vector import ElasticsearchVectorStore

    store = ElasticsearchVectorStore(
        embedding_space=EmbeddingSpace.MOCK_128,
        dims=dims,
        url=_URL,
        index_prefix=f"mws-test-{uuid.uuid4().hex[:8]}",
    )
    return store


def _cleanup(store) -> None:
    store._client.indices.delete(index=store._index, ignore_unavailable=True)


def test_es_add_search_size_and_remove() -> None:
    store = _store(dims=8)
    try:
        rng = np.random.default_rng(0)
        vecs = {f"v{i}": rng.standard_normal(8).astype(np.float32) for i in range(10)}
        for aid, v in vecs.items():
            store.add(aid, v)
        assert store.size == 10

        hits = store.search(vecs["v3"], top_k=3)
        assert hits[0][0] == "v3"  # nearest neighbor of v3 is itself
        assert -1.0 <= hits[0][1] <= 1.0001  # cosine range
        assert hits[0][1] > 0.99  # self-similarity ≈ 1

        store.remove("v3")
        assert store.size == 9
    finally:
        _cleanup(store)


def test_es_matches_inmemory_topk() -> None:
    """Parity: ES kNN agrees with the in-memory brute-force store on the top
    hit and largely on the top-k set.

    ES kNN is approximate (HNSW), so a single boundary swap at rank-k against
    the exact brute-force ranking is expected on near-tie random vectors — we
    require the top-1 to match and at least a 2/3 overlap (same tolerance the
    LanceDB parity test uses for the ANN boundary)."""
    from mws.storage.vector import VectorStore

    store = _store(dims=8)
    try:
        rng = np.random.default_rng(1)
        vecs = {f"v{i}": rng.standard_normal(8).astype(np.float32) for i in range(12)}
        mem = VectorStore(embedding_space=EmbeddingSpace.MOCK_128, dims=8)
        for aid, v in vecs.items():
            store.add(aid, v)
            mem.add(aid, v)

        query = vecs["v5"]
        es_top = [aid for aid, _ in store.search(query, top_k=3)]
        mem_top = [aid for aid, _ in mem.search(query, top_k=3)]
        assert es_top[0] == mem_top[0] == "v5"  # nearest neighbor exact
        assert len(set(es_top) & set(mem_top)) >= 2  # ANN boundary tolerance
    finally:
        _cleanup(store)


def test_es_idempotent_readd() -> None:
    store = _store(dims=4)
    try:
        v = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        store.add("a", v)
        store.add("a", v)  # same id must not duplicate
        assert store.size == 1
    finally:
        _cleanup(store)


def test_es_dim_mismatch_raises() -> None:
    store = _store(dims=4)
    try:
        with pytest.raises(ValueError, match="Expected 4-d"):
            store.add("bad", np.zeros(3, dtype=np.float32))
    finally:
        _cleanup(store)


def test_es_registry_selection_runs_engine() -> None:
    """vector_backend='elasticsearch' wires an ES store the engine can use."""
    from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
    from mws.core.types import Modality
    from mws.embedding.mock import MockEmbedder
    from mws.retrieval.engine import RetrievalEngine
    from mws.retrieval.query import RetrievalQuery
    from mws.storage.registry import StoreRegistry

    # Skip-guard via _store(), then build a registry on ES.
    probe = _store(dims=128)
    _cleanup(probe)

    stores = StoreRegistry(
        embedding_space=EmbeddingSpace.MOCK_128,
        embedding_dims=128,
        vector_backend="elasticsearch",
        elasticsearch_url=_URL,
    )
    try:
        from mws.storage.vector import ElasticsearchVectorStore

        assert isinstance(stores.vector, ElasticsearchVectorStore)
        engine = RetrievalEngine(stores=stores, embedder=MockEmbedder(seed=0))
        for i, text in enumerate(["motor anomaly", "belt normal", "motor vibration"]):
            atom = ExperienceAtom(
                modality=Modality.TELEMETRY,
                coord=SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=float(i)),
                text_summary=text,
            )
            atom.provenance.add("test", "sensor", "created")
            engine.ingest(atom)
        results = engine.search(RetrievalQuery(text="motor issue", top_k=2))
        assert len(results) >= 1
    finally:
        stores.vector._client.indices.delete(index=stores.vector._index, ignore_unavailable=True)
