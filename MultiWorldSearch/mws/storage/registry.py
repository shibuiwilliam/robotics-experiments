"""Polyglot store registry — unified access to all store types."""

from __future__ import annotations

from pathlib import Path

from mws.core.logging import get_logger
from mws.core.types import EmbeddingSpace
from mws.storage.blob import BlobStore
from mws.storage.graph import GraphStore
from mws.storage.spatial import SpatialIndex
from mws.storage.timeseries import DuckDBTimeseriesStore, TimeseriesStore
from mws.storage.vector import LanceDBVectorStore, VectorStore

logger = get_logger(__name__)


class StoreRegistry:
    """Central registry holding all polyglot stores.

    Provides a single access point for the retrieval engine. Backends are
    selectable: the default in-memory stores are dependency-light and fast for
    prototype corpora; ``vector_backend="lancedb"`` and
    ``timeseries_backend="duckdb"`` swap in the real declared databases for
    scale (PROJECT.md §5.1).
    """

    def __init__(
        self,
        embedding_space: EmbeddingSpace = EmbeddingSpace.MOCK_128,
        embedding_dims: int = 128,
        blob_path: Path | None = None,
        vector_backend: str = "memory",
        timeseries_backend: str = "memory",
        lancedb_path: Path | None = None,
    ) -> None:
        if vector_backend == "lancedb":
            logger.info("Using LanceDB vector backend", dims=embedding_dims)
            self.vector: VectorStore | LanceDBVectorStore = LanceDBVectorStore(
                embedding_space=embedding_space, dims=embedding_dims, db_path=lancedb_path
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
