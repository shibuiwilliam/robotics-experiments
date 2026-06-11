"""Local student embedder — the on-device hot-path tier of H7.

Two implementations behind the same interface:

- :class:`LocalStudentEmbedder` — REAL on-device inference via
  sentence-transformers (default model: EmbeddingGemma). Requires the
  ``student`` extra (``uv sync --extra student``). ``is_stub = False``.
- :class:`StubStudentEmbedder` — deterministic hash-based placeholder
  (delegates to MockEmbedder). Used as an explicit, warned fallback when the
  student extra is not installed. ``is_stub = True`` so callers/reports can
  detect it and must not claim H7 is validated on it.

Embedding-space discipline: every student model maps to its own
EmbeddingSpace; unknown models are rejected rather than silently tagged.
"""

from __future__ import annotations

import xxhash

from mws.core.logging import get_logger
from mws.core.types import EmbeddingSpace
from mws.embedding.base import EmbeddingResult
from mws.embedding.mock import MockEmbedder

logger = get_logger(__name__)

# Known student models → their embedding space. Add an EmbeddingSpace member
# before adding a model here (never mix unlabeled spaces).
_MODEL_SPACES: dict[str, EmbeddingSpace] = {
    "google/embeddinggemma-300m": EmbeddingSpace.GEMMA_768,
    "sentence-transformers/all-MiniLM-L6-v2": EmbeddingSpace.MINILM_384,
}

_WARNED_STUB = False


class LocalStudentEmbedder:
    """Real on-device student embedder (sentence-transformers).

    Loads the model lazily on first use. Raises ImportError with install
    instructions if the ``student`` extra is missing.
    """

    #: Real model — H7 comparisons against the teacher are meaningful.
    is_stub: bool = False
    #: Latency-decomposition bucket (CLAUDE.md §10): on-device, no cloud.
    latency_bucket = "local_embed"

    def __init__(self, model_name: str = "google/embeddinggemma-300m") -> None:
        if model_name not in _MODEL_SPACES:
            raise ValueError(
                f"Unknown student model '{model_name}'. Add an EmbeddingSpace "
                "member and register it in _MODEL_SPACES first (embedding-space "
                "discipline: never index vectors from an unlabeled space)."
            )
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - exercised via factory fallback
            raise ImportError(
                "The real student embedder needs sentence-transformers. "
                "Install with: uv sync --extra student"
            ) from exc

        self._model_name = model_name
        self._space = _MODEL_SPACES[model_name]
        logger.info("Loading local student model", model=model_name)
        self._model = SentenceTransformer(model_name)
        self._dims = int(self._model.get_sentence_embedding_dimension() or 0)

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    @property
    def dims(self) -> int:
        return self._dims

    def _content_hash(self, text: str) -> str:
        return xxhash.xxh64(text.encode()).hexdigest()

    def format_document(self, text: str) -> str:
        """Symmetric embedder: no task formatting (identity)."""
        return text

    def format_query(self, text: str) -> str:
        """Symmetric embedder: no task formatting (identity)."""
        return text

    def embed_document(self, text: str) -> EmbeddingResult:
        return self.embed_text(self.format_document(text))

    def embed_query(self, text: str) -> EmbeddingResult:
        return self.embed_text(self.format_query(text))

    def embed_text(self, text: str) -> EmbeddingResult:
        vector = self._model.encode([text], normalize_embeddings=True)[0]
        return EmbeddingResult(
            vector=[float(x) for x in vector],
            space=self._space,
            content_hash=self._content_hash(text),
            dims=self._dims,
        )

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [
            EmbeddingResult(
                vector=[float(x) for x in vec],
                space=self._space,
                content_hash=self._content_hash(text),
                dims=self._dims,
            )
            for text, vec in zip(texts, vectors, strict=True)
        ]


class StubStudentEmbedder(MockEmbedder):
    """Deterministic hash-based placeholder for the student tier.

    NOT a real model: vectors are seeded pseudo-random. ``is_stub`` is True so
    reports can detect it — H7 must never be claimed validated on this class.
    """

    is_stub: bool = True

    def __init__(
        self,
        space: EmbeddingSpace = EmbeddingSpace.GEMMA_128,
        dims: int = 128,
        seed: int = 0,
    ) -> None:
        super().__init__(space=space, dims=dims, seed=seed)
        global _WARNED_STUB
        if not _WARNED_STUB:
            logger.warning(
                "StubStudentEmbedder in use (sentence-transformers not installed); "
                "vectors are deterministic mock vectors. H7 (teacher/student) is "
                "NOT validated on this path. Install with: uv sync --extra student",
                space=str(space),
                dims=dims,
            )
            _WARNED_STUB = True
