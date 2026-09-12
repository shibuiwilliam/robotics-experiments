import pytest
from typer.testing import CliRunner

from gtwm.cli import app

pytestmark = pytest.mark.unit

runner = CliRunner()

EXPECTED_SUBCOMMANDS = ["doctor", "sim", "kg", "wm", "ground", "exp", "whatif", "llm"]


def test_cli_help_lists_all_subcommands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in EXPECTED_SUBCOMMANDS:
        assert name in result.stdout


def test_cli_doctor_runs() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0


def test_cli_unimplemented_subcommand_exits_nonzero() -> None:
    result = runner.invoke(app, ["sim"])
    assert result.exit_code == 1
