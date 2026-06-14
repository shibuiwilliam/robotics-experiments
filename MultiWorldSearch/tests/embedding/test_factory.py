"""Tests for the embedder factory.

One real embedding model (gemini-embedding-2); MockEmbedder is its offline
deterministic stand-in. No teacher/student role selection.
"""

import pytest

from mws.core.config import MWSSettings
from mws.core.types import CloudMode
from mws.embedding.factory import create_embedder
from mws.embedding.mock import MockEmbedder


def test_mock_mode_returns_mock() -> None:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=42)
    emb = create_embedder(settings)
    assert isinstance(emb, MockEmbedder)
    assert emb.space == settings.default_embedding_space


def test_live_requires_api_key() -> None:
    """Live mode without API key should raise ValueError."""
    settings = MWSSettings(cloud_mode=CloudMode.LIVE, google_api_key="")
    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        create_embedder(settings)


def test_live_returns_gemini_embedder() -> None:
    """Live mode returns the single real model: gemini-embedding-2."""
    from mws.core.types import EmbeddingSpace

    settings = MWSSettings(cloud_mode=CloudMode.LIVE, google_api_key="test-key-not-real")
    emb = create_embedder(settings)
    # gemini-embedding-2 with the v2 asymmetric space; constructed without a call.
    assert type(emb).__name__ == "GeminiTeacherEmbedder"
    assert emb.space == EmbeddingSpace.GEMINI_768_V2


def test_mock_mode_uses_config_dims() -> None:
    """Verify mock embedder uses dims from settings, not hardcoded."""
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, embedding_dims=256)
    emb = create_embedder(settings)
    assert emb.dims == 256
