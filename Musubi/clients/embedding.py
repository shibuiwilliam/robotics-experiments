"""Embedding adapter — gemini-embedding-2 behind the VCR, with a persistent local cache.

Every embedding is cached by ``hash(model, dim, content)`` so we never bill twice (NFR-COST,
CLAUDE.md §8). Dimensions default to 768 (MRL, unit-normalized); mixing with gemini-embedding-001
is forbidden (space-incompatible) — the model id is fixed in the cache key.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from clients.backends import EmbeddingBackend, FakeGeminiClient
from clients.vcr import VCR
from config import load_registry

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CACHE = _REPO_ROOT / "data" / "embeddings.sqlite"

_SCHEMA = "CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector TEXT NOT NULL);"


class EmbeddingAdapter:
    """VCR-wrapped embedding client with a SQLite cache. Offline uses FakeGeminiClient."""

    def __init__(
        self,
        vcr: VCR | None = None,
        backend: EmbeddingBackend | None = None,
        cache_path: Path | str | None = None,
    ) -> None:
        self._vcr = vcr or VCR()
        self._backend = backend or FakeGeminiClient()
        reg = load_registry()
        self._model = reg.model_id("embedding")
        self._dim = int(reg.require("models.embedding.dimensions"))
        path = Path(cache_path) if cache_path is not None else _DEFAULT_CACHE
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(_SCHEMA)
        self._hits = 0

    def _key(self, content: str) -> str:
        raw = f"{self._model}|{self._dim}|{content}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @property
    def cache_hits(self) -> int:
        return self._hits

    def embed(self, content: str) -> list[float]:
        key = self._key(content)
        row = self._conn.execute("SELECT vector FROM embeddings WHERE key = ?", (key,)).fetchone()
        if row is not None:
            self._hits += 1
            vector: list[float] = json.loads(row[0])
            return vector
        request = {"content_hash": key}
        result: list[float] = self._vcr.interact(
            self._model,
            request,
            live_fn=lambda: self._backend.embed(content, self._model, self._dim),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO embeddings (key, vector) VALUES (?, ?)",
            (key, json.dumps(result)),
        )
        self._conn.commit()
        return result

    def embed_many(self, contents: list[str]) -> list[list[float]]:
        return [self.embed(c) for c in contents]

    def close(self) -> None:
        self._conn.close()
