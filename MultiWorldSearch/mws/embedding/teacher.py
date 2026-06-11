"""Gemini Embedding 2 adapter — live cloud path, behind MWS_CLOUD_MODE.

Only used for offline/batch indexing. Never on the hot path.
Uses the google-genai SDK: https://ai.google.dev/gemini-api/docs/embeddings

Doc-compliance notes (IMPROVEMENT P9, verified against the official docs
2026-06-11):

- gemini-embedding-2 does NOT take a ``task_type`` parameter. Retrieval
  asymmetry is expressed with PROMPT PREFIXES instead (E1):
      query side:    ``task: search result | query: {content}``
      document side: ``title: {title} | text: {content}`` (``title: none``
      when there is no title — our atoms have none).
  The prefixed scheme changes what the coordinates mean, so it lives in its
  own embedding space (gemini2-768-v2) — never compared with v1 vectors.
- ``embed_batch`` issues ONE API request for the whole list (one Content per
  text → one embedding per text). Chunking to a configured size is the
  caller's job (RetrievalEngine.ingest_batch).
- Truncated output dimensions (768/1536) are auto-normalized by the model
  (E4 — asserted by a live test).
"""

from __future__ import annotations

import xxhash

from mws.core.logging import get_logger
from mws.core.types import EmbeddingSpace
from mws.embedding.base import EmbeddingResult

logger = get_logger(__name__)

# Default model for embedding
GEMINI_EMBEDDING_MODEL = "gemini-embedding-2"

# Official task-instruction prefixes (asymmetric retrieval, E1).
QUERY_PREFIX_FMT = "task: search result | query: {content}"
DOCUMENT_PREFIX_FMT = "title: none | text: {content}"


class GeminiTeacherEmbedder:
    #: Latency-decomposition bucket (CLAUDE.md §10). Real cloud round-trip.
    latency_bucket = "gemini_embed"

    """Adapter for Gemini Embedding 2 (cloud, offline/batch only).

    In mock mode this class is never instantiated — the MockEmbedder is used
    instead. This class is only created when MWS_CLOUD_MODE=live.
    """

    def __init__(
        self,
        api_key: str,
        space: EmbeddingSpace = EmbeddingSpace.GEMINI_768_V2,
        dims: int = 768,
        model: str = GEMINI_EMBEDDING_MODEL,
    ) -> None:
        from google import genai

        self._api_key = api_key
        self._space = space
        self._dims = dims
        self._model = model
        self._client = genai.Client(api_key=api_key)
        logger.info(
            "GeminiTeacherEmbedder initialized",
            model=model,
            space=str(space),
            dims=dims,
        )

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    @property
    def dims(self) -> int:
        return self._dims

    def _content_hash(self, text: str) -> str:
        """Produce a deterministic hash for caching."""
        return xxhash.xxh64(text.encode()).hexdigest()

    def format_document(self, text: str) -> str:
        """Final string embedded for the DOCUMENT (index) side (E1)."""
        return DOCUMENT_PREFIX_FMT.format(content=text)

    def format_query(self, text: str) -> str:
        """Final string embedded for the QUERY (search) side (E1)."""
        return QUERY_PREFIX_FMT.format(content=text)

    def _embed_config(self):
        from google.genai import types

        return types.EmbedContentConfig(output_dimensionality=self._dims)

    def embed_text(self, text: str) -> EmbeddingResult:
        """Embed text as-is via Gemini Embedding 2 API (one request).

        Uses output_dimensionality to control vector size (MRL truncation).
        Content hash covers exactly the string sent to the API, so document
        and query embeddings of the same raw text cache separately (correct).
        """
        result = self._client.models.embed_content(
            model=self._model,
            contents=text,
            config=self._embed_config(),
        )
        embedding = result.embeddings[0] if result.embeddings else None
        if embedding is None or embedding.values is None:
            raise RuntimeError(f"Gemini embedding returned no data for text: {text[:50]}")
        vector = list(embedding.values)

        # Reconciliation marker: exactly one line per real API REQUEST
        # (mws/eval/reconcile.py counts these).
        logger.debug(
            "Gemini embedding call",
            model=self._model,
            n_texts=1,
            text_len=len(text),
            dims=len(vector),
        )

        return EmbeddingResult(
            vector=vector,
            space=self._space,
            content_hash=self._content_hash(text),
            dims=len(vector),
        )

    def embed_document(self, text: str) -> EmbeddingResult:
        return self.embed_text(self.format_document(text))

    def embed_query(self, text: str) -> EmbeddingResult:
        return self.embed_text(self.format_query(text))

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Embed N texts in ONE API request (E2).

        One ``Content`` object per text yields one embedding per text. The
        caller chunks to the configured batch size; this method never splits
        the request itself, so request accounting stays 1 call = 1 request.
        """
        if not texts:
            return []
        from google.genai import types

        contents = [types.Content(parts=[types.Part.from_text(text=t)]) for t in texts]
        result = self._client.models.embed_content(
            model=self._model,
            contents=contents,
            config=self._embed_config(),
        )
        embeddings = result.embeddings or []
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"Gemini batch embedding returned {len(embeddings)} vectors for {len(texts)} texts"
            )

        # One marker line per real API REQUEST (not per text).
        logger.debug(
            "Gemini embedding call",
            model=self._model,
            n_texts=len(texts),
            dims=self._dims,
        )

        results: list[EmbeddingResult] = []
        for text, embedding in zip(texts, embeddings, strict=True):
            if embedding.values is None:
                raise RuntimeError(f"Gemini batch embedding missing vector for: {text[:50]}")
            results.append(
                EmbeddingResult(
                    vector=list(embedding.values),
                    space=self._space,
                    content_hash=self._content_hash(text),
                    dims=len(embedding.values),
                )
            )
        return results
