"""Tests for embedder factory."""

import pytest

from mws.core.config import MWSSettings
from mws.core.types import CloudMode
from mws.embedding.factory import create_embedder
from mws.embedding.mock import MockEmbedder


def test_mock_mode_returns_mock() -> None:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=42)
    emb = create_embedder(settings, role="student")
    assert isinstance(emb, MockEmbedder)
    assert emb.space == settings.default_embedding_space


def test_mock_mode_teacher_also_mock() -> None:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK)
    emb = create_embedder(settings, role="teacher")
    assert isinstance(emb, MockEmbedder)


def test_live_teacher_requires_api_key() -> None:
    """Live mode teacher without API key should raise ValueError."""
    settings = MWSSettings(cloud_mode=CloudMode.LIVE, google_api_key="")
    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        create_embedder(settings, role="teacher")


def test_mock_mode_uses_config_dims() -> None:
    """Verify mock embedder uses dims from settings, not hardcoded."""
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, embedding_dims=256)
    emb = create_embedder(settings)
    assert emb.dims == 256
