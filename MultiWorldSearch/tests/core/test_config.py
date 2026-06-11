"""Tests for MWS config loading."""

import os

from mws.core.config import MWSSettings
from mws.core.types import CloudMode


def test_default_settings() -> None:
    settings = MWSSettings()
    assert settings.cloud_mode == CloudMode.MOCK
    assert settings.seed == 0
    assert settings.embedding_dims == 128


def test_settings_from_env(monkeypatch: object) -> None:
    os.environ["MWS_CLOUD_MODE"] = "mock"
    os.environ["MWS_SEED"] = "42"
    try:
        settings = MWSSettings()
        assert settings.cloud_mode == CloudMode.MOCK
        assert settings.seed == 42
    finally:
        os.environ.pop("MWS_CLOUD_MODE", None)
        os.environ.pop("MWS_SEED", None)
