"""Tests for menagerie model resolution."""

from __future__ import annotations

import pytest

from sim.menagerie import get_model_path, list_available_models


@pytest.mark.unit
class TestMenagerie:
    def test_panda_resolves(self) -> None:
        path = get_model_path("panda")
        assert path.exists()
        assert path.name == "panda_minimal.xml"

    def test_drone_resolves(self) -> None:
        path = get_model_path("drone")
        assert path.exists()
        assert path.name == "drone_minimal.xml"

    def test_unknown_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            get_model_path("nonexistent_robot_xyz")

    def test_list_models(self) -> None:
        models = list_available_models()
        assert "panda" in models
        assert "drone" in models
