"""Tests for the real DuckDB / LanceDB storage backends (H2).

These use the embedded, local databases (no cloud, no live marker needed).
They verify the declared dependencies are genuinely exercised and that the
backends behave equivalently to the in-memory stores on the query paths the
retrieval engine uses.
"""

from __future__ import annotations

import numpy as np

from mws.core.types import EmbeddingSpace
from mws.storage.registry import StoreRegistry
from mws.storage.timeseries import DuckDBTimeseriesStore, TimeseriesRecord, TimeseriesStore
from mws.storage.vector import LanceDBVectorStore, VectorStore


def _records() -> list[TimeseriesRecord]:
    return [
        TimeseriesRecord("a", 1.0, {"trust": 0.9}, {"k": "x"}),
        TimeseriesRecord("b", 2.0, {"trust": 0.8}, {"k": "y"}),
        TimeseriesRecord("c", 3.0, {"trust": 0.7}, {"k": "x"}),
    ]


def test_duckdb_timeseries_matches_inmemory_range() -> None:
    mem = TimeseriesStore()
    duck = DuckDBTimeseriesStore()
    for r in _records():
        mem.add(r)
        duck.add(r)
    assert duck.size == mem.size == 3

    mem_ids = [r.atom_id for r in mem.query_range(1.5, 3.5)]
    duck_ids = [r.atom_id for r in duck.query_range(1.5, 3.5)]
    assert duck_ids == mem_ids == ["b", "c"]


def test_duckdb_timeseries_tag_filter_is_sql() -> None:
    duck = DuckDBTimeseriesStore()
    for r in _records():
        duck.add(r)
    got = duck.query_range(0.0, 10.0, tag_filter={"k": "x"})
    assert [r.atom_id for r in got] == ["a", "c"]
    latest = duck.latest(1)
    assert latest[-1].atom_id == "c"


def test_lancedb_vector_matches_inmemory_topk() -> None:
    rng = np.random.default_rng(0)
    vecs = {f"v{i}": rng.standard_normal(8).astype(np.float32) for i in range(10)}

    mem = VectorStore(embedding_space=EmbeddingSpace.MOCK_128, dims=8)
    lance = LanceDBVectorStore(embedding_space=EmbeddingSpace.MOCK_128, dims=8)
    for aid, v in vecs.items():
        mem.add(aid, v)
        lance.add(aid, v)
    assert lance.size == mem.size == 10

    query = vecs["v3"]
    mem_top = [aid for aid, _ in mem.search(query, top_k=3)]
    lance_top = [aid for aid, _ in lance.search(query, top_k=3)]
    # The nearest neighbour to v3 is itself in both backends.
    assert mem_top[0] == "v3"
    assert lance_top[0] == "v3"
    # Top-3 sets agree (ordering of near-ties may differ slightly).
    assert set(lance_top) == set(mem_top)


def test_lancedb_idempotent_readd() -> None:
    lance = LanceDBVectorStore(embedding_space=EmbeddingSpace.MOCK_128, dims=4)
    v = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    lance.add("a", v)
    lance.add("a", v)  # re-add same id must not duplicate
    assert lance.size == 1


def test_retrieval_engine_runs_on_real_backends() -> None:
    """End-to-end: the RetrievalEngine ingests and searches correctly when the
    vector index is LanceDB and the timeseries index is DuckDB — i.e. the
    declared databases are exercised on the actual retrieval path."""
    from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
    from mws.core.types import Modality
    from mws.embedding.mock import MockEmbedder
    from mws.retrieval.engine import RetrievalEngine
    from mws.retrieval.query import RetrievalQuery

    stores = StoreRegistry(
        embedding_space=EmbeddingSpace.MOCK_128,
        embedding_dims=128,
        vector_backend="lancedb",
        timeseries_backend="duckdb",
    )
    engine = RetrievalEngine(
        stores=stores,
        embedder=MockEmbedder(space=EmbeddingSpace.MOCK_128, dims=128, seed=0),
    )
    for i in range(5):
        engine.ingest(
            ExperienceAtom(
                modality=Modality.STRUCTURED_RECORD,
                coord=SpatiotemporalCoord(x=float(i), y=0.0, z=0.0, timestamp=100.0 + i),
                text_summary=f"pump bearing maintenance record {i}",
                entity_id=f"pump_{i}",
                tags=["maintenance", f"pump_{i}"],
            )
        )
    results = engine.search(
        RetrievalQuery(text="pump bearing maintenance record 2", tags=["maintenance"], top_k=3)
    )
    assert len(results) > 0
    assert stores.vector.size == 5
    assert stores.timeseries.size == 5


def test_registry_selects_real_backends() -> None:
    reg = StoreRegistry(
        embedding_space=EmbeddingSpace.MOCK_128,
        embedding_dims=8,
        vector_backend="lancedb",
        timeseries_backend="duckdb",
    )
    assert isinstance(reg.vector, LanceDBVectorStore)
    assert isinstance(reg.timeseries, DuckDBTimeseriesStore)
    # Default registry uses in-memory backends.
    default = StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=8)
    assert isinstance(default.vector, VectorStore)
    assert isinstance(default.timeseries, TimeseriesStore)
