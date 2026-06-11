"""Typed settings for MWS, loaded from env vars.

.env file is loaded by the CLI entrypoint (cli.py), not here. This ensures
tests run with defaults (mock mode) unless explicitly overridden.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from mws.core.types import CloudMode, EmbeddingSpace


class MWSSettings(BaseSettings):
    """Global MWS settings. Env vars prefixed with MWS_."""

    model_config = SettingsConfigDict(
        env_prefix="MWS_",
        extra="ignore",
    )

    cloud_mode: CloudMode = Field(
        default=CloudMode.MOCK,
        description="mock (default, offline) or live (requires GOOGLE_API_KEY)",
    )
    run_dir: Path = Field(
        default=Path("./runs"),
        description="Directory for experiment artifacts (append-only)",
    )
    data_dir: Path = Field(
        default=Path("./data"),
        description="Directory for generated/synthetic data",
    )
    seed: int = Field(default=0, description="Global random seed")
    default_embedding_space: EmbeddingSpace = Field(
        default=EmbeddingSpace.MOCK_128,
        description="Default embedding space for mock mode",
    )
    embedding_dims: int = Field(default=128, description="Default embedding dimensions")
    embedding_batch_size: int = Field(
        default=64,
        description="Max texts per single embedding API request (IMPROVEMENT E2)",
    )
    student_model: str = Field(
        default="google/embeddinggemma-300m",
        description="Local student embedding model id (H7). Requires the 'student' extra.",
    )
    google_api_key: str = Field(default="", description="Gemini API key (live mode only)")

    @classmethod
    def from_env(cls) -> MWSSettings:
        """Load settings from environment variables."""
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        return cls(google_api_key=api_key)


def get_settings() -> MWSSettings:
    """Get the global settings singleton."""
    return MWSSettings.from_env()
