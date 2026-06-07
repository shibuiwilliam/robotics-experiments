"""Tests for execution mode detection and fallback behavior."""

from __future__ import annotations

import pytest

from eval.scenarios.execution_mode import ExecutionMode, detect_mode


@pytest.mark.unit
class TestExecutionMode:
    def test_explicit_offline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PSL_MODE", "offline")
        assert detect_mode() == ExecutionMode.OFFLINE

    def test_explicit_online_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PSL_MODE", "online")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        # SDK is installed in this env, so should be ONLINE
        assert detect_mode() == ExecutionMode.ONLINE

    def test_explicit_online_without_key_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PSL_MODE", "online")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.warns(RuntimeWarning, match="ANTHROPIC_API_KEY not set"):
            result = detect_mode()
        assert result == ExecutionMode.OFFLINE

    def test_auto_detect_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PSL_MODE", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        # Auto: should be ONLINE since key is set and SDK is importable
        assert detect_mode() == ExecutionMode.ONLINE

    def test_auto_detect_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PSL_MODE", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert detect_mode() == ExecutionMode.OFFLINE

    def test_enum_values(self) -> None:
        assert ExecutionMode.OFFLINE.value == "offline"
        assert ExecutionMode.ONLINE.value == "online"
