"""Polyglot store registry — unified access to all store types."""

from __future__ import annotations

from pathlib import Path

from mws.core.logging import get_logger
from mws.core.types import EmbeddingSpace
from mws.storage.blob import BlobStore
from mws.storage.graph import GraphStore
from mws.storage.spatial import SpatialIndex
from mws.storage.timeseries import DuckDBTimeseriesStore, TimeseriesStore
from mws.storage.vector import (
    ElasticsearchVectorStore,
    LanceDBVectorStore,
    VectorStore,
)

logger = get_logger(__name__)


class StoreRegistry:
    """Central registry holding all polyglot stores.

    Provides a single access point for the retrieval engine. Backends are
    selectable: the default in-memory stores are dependency-light and fast for
    prototype corpora; ``vector_backend="lancedb"`` (embedded ANN on disk) or
    ``vector_backend="elasticsearch"`` (kNN via docker-compose), and
    ``timeseries_backend="duckdb"`` swap in the real databases for scale
    (PROJECT.md §5.1).
    """

    def __init__(
        self,
        embedding_space: EmbeddingSpace = EmbeddingSpace.MOCK_128,
        embedding_dims: int = 128,
        blob_path: Path | None = None,
        vector_backend: str = "memory",
        timeseries_backend: str = "memory",
        lancedb_path: Path | None = None,
        elasticsearch_url: str = "http://localhost:9200",
    ) -> None:
        self.vector: VectorStore | LanceDBVectorStore | ElasticsearchVectorStore
        if vector_backend == "lancedb":
            logger.info("Using LanceDB vector backend", dims=embedding_dims)
            self.vector = LanceDBVectorStore(
                embedding_space=embedding_space, dims=embedding_dims, db_path=lancedb_path
            )
        elif vector_backend == "elasticsearch":
            import uuid

            # Unique index per registry instance → isolation matching the
            # in-memory store (engine, federated instances and consolidation
            # re-index must not share one index). Dropped by close().
            self.vector = ElasticsearchVectorStore(
                embedding_space=embedding_space,
                dims=embedding_dims,
                url=elasticsearch_url,
                index_suffix=uuid.uuid4().hex[:12],
            )
        else:
            self.vector = VectorStore(embedding_space=embedding_space, dims=embedding_dims)

        if timeseries_backend == "duckdb":
            logger.info("Using DuckDB timeseries backend")
            self.timeseries: TimeseriesStore | DuckDBTimeseriesStore = DuckDBTimeseriesStore()
        else:
            self.timeseries = TimeseriesStore()

        self.graph = GraphStore()
        self.spatial = SpatialIndex()
        self.blob = BlobStore(base_path=blob_path)

    def close(self) -> None:
        """Release external resources (e.g. drop a per-run Elasticsearch index).

        Safe to call on any backend — no-op for the in-memory/LanceDB stores.
        """
        drop = getattr(self.vector, "drop", None)
        if callable(drop):
            drop()
