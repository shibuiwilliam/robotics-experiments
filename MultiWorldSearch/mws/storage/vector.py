"""Vector store adapters for semantic search.

Three interchangeable backends with the same interface
(``add`` / ``search`` → cosine similarity / ``remove`` / ``size``):

- ``VectorStore`` — dependency-light in-memory brute-force cosine (default).
- ``LanceDBVectorStore`` — real LanceDB ANN index on disk (embedded).
- ``ElasticsearchVectorStore`` — Elasticsearch ``dense_vector`` + kNN, run via
  ``docker-compose.yml``. Selected with ``vector_backend="elasticsearch"``.

Backends are chosen through the store registry / index config; the in-memory
default keeps tests deterministic and offline.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from mws.core.logging import get_logger
from mws.core.types import EmbeddingSpace

logger = get_logger(__name__)


class VectorStore:
    """In-memory vector store with brute-force cosine similarity.

    For the research prototype, this provides a simple, dependency-light vector
    index. LanceDBVectorStore swaps in a real ANN index for larger scale.
    """

    def __init__(self, embedding_space: EmbeddingSpace, dims: int) -> None:
        self.embedding_space = embedding_space
        self.dims = dims
        self._vectors: dict[str, np.ndarray] = {}
        self._metadata: dict[str, dict[str, Any]] = {}

    def add(
        self,
        atom_id: str,
        vector: np.ndarray,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add a vector to the index."""
        if vector.shape != (self.dims,):
            raise ValueError(f"Expected {self.dims}-d vector, got {vector.shape}")
        # Normalize for cosine similarity
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        self._vectors[atom_id] = vector
        self._metadata[atom_id] = metadata or {}

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """Search for nearest neighbors by cosine similarity.

        Returns list of (atom_id, similarity_score) sorted descending.
        """
        if len(self._vectors) == 0:
            return []
        norm = np.linalg.norm(query_vector)
        if norm > 0:
            query_vector = query_vector / norm

        scores: list[tuple[str, float]] = []
        for atom_id, vec in self._vectors.items():
            sim = float(np.dot(query_vector, vec))
            scores.append((atom_id, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def remove(self, atom_id: str) -> None:
        """Remove a vector from the index."""
        self._vectors.pop(atom_id, None)
        self._metadata.pop(atom_id, None)

    @property
    def size(self) -> int:
        return len(self._vectors)


class LanceDBVectorStore:
    """LanceDB-backed vector store (real ANN index on disk).

    Same interface as :class:`VectorStore`. Vectors are stored in a LanceDB
    table and searched with cosine distance; results are returned as cosine
    *similarity* (1 - distance) to match the in-memory store. Re-adding an
    atom_id replaces the prior row.
    """

    def __init__(
        self,
        embedding_space: EmbeddingSpace,
        dims: int,
        db_path: Path | str | None = None,
        table_name: str = "vectors",
    ) -> None:
        import lancedb
        import pyarrow as pa

        self.embedding_space = embedding_space
        self.dims = dims
        self._path = str(db_path) if db_path is not None else tempfile.mkdtemp(prefix="mws_lance_")
        self._db = lancedb.connect(self._path)
        self._schema = pa.schema(
            [
                pa.field("atom_id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dims)),
            ]
        )
        self._table = self._db.create_table(table_name, schema=self._schema, mode="overwrite")

    def add(
        self,
        atom_id: str,
        vector: np.ndarray,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if vector.shape != (self.dims,):
            raise ValueError(f"Expected {self.dims}-d vector, got {vector.shape}")
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        # Replace any existing row for this atom_id (idempotent ingest).
        self._table.delete(f"atom_id = '{atom_id}'")
        self._table.add([{"atom_id": atom_id, "vector": vector.astype(np.float32).tolist()}])

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        if self.size == 0:
            return []
        norm = np.linalg.norm(query_vector)
        if norm > 0:
            query_vector = query_vector / norm
        rows = (
            self._table.search(query_vector.astype(np.float32))
            .metric("cosine")
            .limit(top_k)
            .to_list()
        )
        # LanceDB cosine distance in [0, 2]; convert to similarity in [-1, 1].
        return [(r["atom_id"], 1.0 - float(r["_distance"])) for r in rows]

    def remove(self, atom_id: str) -> None:
        self._table.delete(f"atom_id = '{atom_id}'")

    @property
    def size(self) -> int:
        return int(self._table.count_rows())


def _es_index_name(prefix: str, embedding_space: EmbeddingSpace, dims: int) -> str:
    """Index name namespaced by embedding space + dims (CLAUDE.md §6).

    Different models/dims/schemes must never share an index, so the space tag
    and dims are baked into the index name. ES index names must be lowercase.
    """
    space = str(embedding_space).replace("_", "-").lower()
    return f"{prefix}-{space}-{dims}d"


class ElasticsearchVectorStore:
    """Elasticsearch-backed vector store (``dense_vector`` + kNN).

    Same interface as :class:`VectorStore`. Vectors are L2-normalized on add
    and the index uses cosine similarity, so kNN scores map back to cosine in
    [-1, 1] (ES returns ``(cosine + 1) / 2`` in [0, 1]; we invert it). The
    document id is the ``atom_id`` so re-adding replaces the row (idempotent).

    Run the server with ``docker-compose up -d`` (see docker-compose.yml).
    Requires the optional ``es`` extra (``elasticsearch`` client).
    """

    def __init__(
        self,
        embedding_space: EmbeddingSpace,
        dims: int,
        url: str = "http://localhost:9200",
        index_prefix: str = "mws-vectors",
        refresh: str = "true",
    ) -> None:
        try:
            from elasticsearch import Elasticsearch
        except ImportError as exc:  # pragma: no cover - exercised when extra missing
            raise ImportError(
                "ElasticsearchVectorStore needs the 'es' extra. Install with: uv sync --extra es"
            ) from exc

        self.embedding_space = embedding_space
        self.dims = dims
        self._url = url
        self._index = _es_index_name(index_prefix, embedding_space, dims)
        # refresh="true" on writes makes them immediately searchable
        # (read-after-write); set "false" for bulk loads where latency matters.
        self._refresh = refresh
        self._client = Elasticsearch(url)
        self._ensure_index()
        logger.info("Using Elasticsearch vector backend", url=url, index=self._index, dims=dims)

    def _ensure_index(self) -> None:
        if self._client.indices.exists(index=self._index):
            return
        self._client.indices.create(
            index=self._index,
            mappings={
                "properties": {
                    "vector": {
                        "type": "dense_vector",
                        "dims": self.dims,
                        "index": True,
                        "similarity": "cosine",
                    }
                }
            },
        )

    def add(
        self,
        atom_id: str,
        vector: np.ndarray,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if vector.shape != (self.dims,):
            raise ValueError(f"Expected {self.dims}-d vector, got {vector.shape}")
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        # doc id == atom_id → re-adding replaces the doc (idempotent ingest).
        self._client.index(
            index=self._index,
            id=atom_id,
            document={"vector": vector.astype(np.float32).tolist()},
            refresh=self._refresh,
        )

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        if self.size == 0:
            return []
        norm = np.linalg.norm(query_vector)
        if norm > 0:
            query_vector = query_vector / norm
        resp = self._client.search(
            index=self._index,
            knn={
                "field": "vector",
                "query_vector": query_vector.astype(np.float32).tolist(),
                "k": top_k,
                "num_candidates": max(top_k * 10, 100),
            },
            size=top_k,
            source=False,
        )
        # ES cosine score is (cosine + 1) / 2 ∈ [0, 1]; invert to cosine [-1, 1]
        # so results match the in-memory / LanceDB stores.
        return [(hit["_id"], 2.0 * float(hit["_score"]) - 1.0) for hit in resp["hits"]["hits"]]

    def remove(self, atom_id: str) -> None:
        import contextlib

        from elasticsearch import NotFoundError

        # already absent → idempotent remove
        with contextlib.suppress(NotFoundError):
            self._client.delete(index=self._index, id=atom_id, refresh=self._refresh)

    @property
    def size(self) -> int:
        self._client.indices.refresh(index=self._index)
        return int(self._client.count(index=self._index)["count"])
