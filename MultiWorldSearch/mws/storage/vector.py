"""Vector store adapters for semantic search.

Two interchangeable backends with the same interface:
- ``VectorStore`` — dependency-light in-memory brute-force cosine (default).
- ``LanceDBVectorStore`` — real LanceDB ANN index, selected via the store
  registry / index config. Use for larger corpora.
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
