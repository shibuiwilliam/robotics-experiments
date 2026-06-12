"""足場の健全性テスト: 全パッケージが import 可能で、CLI が起動すること。"""

import importlib

import pytest
from typer.testing import CliRunner

SUBPACKAGES = [
    "common",
    "sim",
    "skills",
    "business",
    "perception",
    "anchoring",
    "kg",
    "agent",
    "oracle",
    "replay",
    "exp",
]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    importlib.import_module(f"orx.{name}")


def test_cli_help() -> None:
    from orx.cli import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "orx" in result.output


def test_cli_version() -> None:
    from orx import __version__
    from orx.cli import app

    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output
