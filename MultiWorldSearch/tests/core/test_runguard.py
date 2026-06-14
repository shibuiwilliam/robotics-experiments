"""Tests for the live-spend gate and mode banner (IMPROVEMENT G1/G2/G5)."""

from __future__ import annotations

import pytest

from mws.core.config import MWSSettings
from mws.core.runguard import (
    LIVE_SPEND_ENV,
    format_mode_banner,
    is_live_spend,
    live_spend_refusal,
)
from mws.core.types import CloudMode


def test_mock_is_not_live_spend() -> None:
    s = MWSSettings(cloud_mode=CloudMode.MOCK)
    assert is_live_spend(s) is False
    assert live_spend_refusal(s) is None


def test_live_replay_is_not_live_spend() -> None:
    """Replay reuses recorded responses → live mode but zero cloud spend."""
    s = MWSSettings(cloud_mode=CloudMode.LIVE, llm_replay="runs/x/llm_calls.jsonl")
    assert is_live_spend(s) is False
    assert live_spend_refusal(s) is None


def test_live_without_confirmation_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LIVE_SPEND_ENV, raising=False)
    s = MWSSettings(cloud_mode=CloudMode.LIVE)
    msg = live_spend_refusal(s, n_scenarios=7)
    assert msg is not None
    assert LIVE_SPEND_ENV in msg
    assert "Refusing" in msg
    assert "$" in msg  # shows an estimated cost


def test_live_with_confirmation_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LIVE_SPEND_ENV, "1")
    s = MWSSettings(cloud_mode=CloudMode.LIVE)
    assert live_spend_refusal(s) is None


def test_banner_contains_mode_backend_seed() -> None:
    s = MWSSettings(cloud_mode=CloudMode.MOCK, vector_backend="memory")
    banner = format_mode_banner(s, seed=3)
    assert "cloud_mode=mock" in banner
    assert "vector_backend=memory" in banner
    assert "seed=3" in banner
    assert "single-seed" in banner  # G5: point-estimate disclosure


def test_banner_flags_real_spend_for_live() -> None:
    s = MWSSettings(cloud_mode=CloudMode.LIVE)
    banner = format_mode_banner(s, seed=0)
    assert "REAL SPEND" in banner
    assert "gemini-embedding-2" in banner
