"""Embedder factory — respects CloudMode to return the mock or live embedder.

There is exactly ONE real embedding model: Gemini ``gemini-embedding-2``
(``GeminiTeacherEmbedder``). The mock embedder is its deterministic,
offline stand-in for tests/CI — not a second model. No on-device "student"
tier (the teacher/student two-tier / H7 idea was retired in favor of a
single cloud model; latency is addressed by batching, the Batch API, and the
content-hash cache instead).
"""

from __future__ import annotations

from typing import Any

from mws.core.config import MWSSettings
from mws.core.logging import get_logger
from mws.core.types import CloudMode, EmbeddingSpace
from mws.embedding.mock import MockEmbedder

logger = get_logger(__name__)


def create_embedder(settings: MWSSettings | None = None) -> Any:
    """Create the embedder for the current cloud mode.

    Returns:
        - mock mode → :class:`MockEmbedder` (deterministic, offline).
        - live mode → :class:`GeminiTeacherEmbedder` (gemini-embedding-2).

    Raises:
        ValueError: live mode without GOOGLE_API_KEY.
    """
    if settings is None:
        from mws.core.config import get_settings

        settings = get_settings()

    if settings.cloud_mode == CloudMode.MOCK:
        logger.info(
            "Creating mock embedder",
            space=str(settings.default_embedding_space),
            dims=settings.embedding_dims,
        )
        return MockEmbedder(
            space=settings.default_embedding_space,
            dims=settings.embedding_dims,
            seed=settings.seed,
        )

    # Live mode — the one real model: gemini-embedding-2.
    if not settings.google_api_key:
        raise ValueError(
            "MWS_CLOUD_MODE=live requires GOOGLE_API_KEY for gemini-embedding-2. "
            "Set the env var or switch to MWS_CLOUD_MODE=mock."
        )

    from mws.embedding.teacher import GeminiTeacherEmbedder

    # Gemini Embedding 2 supports MRL: output_dimensionality 768/1536/3072.
    # 768 for the research prototype (quality vs cost/speed). Space v2 =
    # asymmetric task-instruction prefixes (E1); v1 vectors are not comparable
    # and must not share an index.
    live_dims = 768
    logger.info("Creating live Gemini embedder", model="gemini-embedding-2", dims=live_dims)
    return GeminiTeacherEmbedder(
        api_key=settings.google_api_key,
        space=EmbeddingSpace.GEMINI_768_V2,
        dims=live_dims,
    )
