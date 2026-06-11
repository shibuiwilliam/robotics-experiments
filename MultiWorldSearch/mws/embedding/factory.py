"""Embedder factory — respects CloudMode to return mock or live embedders."""

from __future__ import annotations

from typing import Any

from mws.core.config import MWSSettings
from mws.core.logging import get_logger
from mws.core.types import CloudMode, EmbeddingSpace
from mws.embedding.mock import MockEmbedder
from mws.embedding.student import LocalStudentEmbedder, StubStudentEmbedder

logger = get_logger(__name__)


def create_embedder(
    settings: MWSSettings | None = None,
    role: str = "student",
) -> Any:
    """Create an embedder based on cloud mode and role.

    Args:
        settings: MWS settings. If None, loads from env.
        role: "student" (hot-path, local) or "teacher" (offline, cloud).

    Returns:
        An embedder instance. In mock mode, always returns MockEmbedder.
        In live mode, student returns LocalStudentEmbedder, teacher returns
        GeminiTeacherEmbedder.

    Raises:
        ValueError: If live mode is requested but GOOGLE_API_KEY is missing.
    """
    if settings is None:
        from mws.core.config import get_settings

        settings = get_settings()

    if settings.cloud_mode == CloudMode.MOCK:
        logger.info(
            "Creating mock embedder",
            role=role,
            space=str(settings.default_embedding_space),
            dims=settings.embedding_dims,
        )
        return MockEmbedder(
            space=settings.default_embedding_space,
            dims=settings.embedding_dims,
            seed=settings.seed,
        )

    # Live mode — validate credentials
    if role == "teacher" and not settings.google_api_key:
        raise ValueError(
            "MWS_CLOUD_MODE=live with role=teacher requires GOOGLE_API_KEY. "
            "Set the env var or switch to MWS_CLOUD_MODE=mock."
        )

    if role == "student":
        # Student is always local, even in live mode. Prefer the REAL on-device
        # model (sentence-transformers / EmbeddingGemma); fall back to the
        # explicit stub with a warning when the 'student' extra is missing.
        try:
            student = LocalStudentEmbedder(model_name=settings.student_model)
            logger.info(
                "Creating local student embedder (real on-device model)",
                model=settings.student_model,
                dims=student.dims,
            )
            return student
        except ImportError:
            return StubStudentEmbedder(
                space=EmbeddingSpace.GEMMA_128,
                dims=settings.embedding_dims,
                seed=settings.seed,
            )

    # role == "teacher" and live mode
    from mws.embedding.teacher import GeminiTeacherEmbedder

    # Gemini Embedding 2 supports MRL: output_dimensionality can be 768, 1536, or 3072.
    # Default to 768 for the research prototype (balance of quality vs cost/speed).
    # Space v2 = asymmetric task-instruction prefixes (E1); v1 vectors are not
    # comparable and must not share an index.
    live_dims = 768
    logger.info("Creating live Gemini teacher embedder", model="gemini-embedding-2", dims=live_dims)
    return GeminiTeacherEmbedder(
        api_key=settings.google_api_key,
        space=EmbeddingSpace.GEMINI_768_V2,
        dims=live_dims,
    )
