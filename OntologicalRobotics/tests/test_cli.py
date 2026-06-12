"""CLI統合テスト（CliRunner、完全オフライン）。

データ書込はテンポラリの runs ルートに隔離する（data/runs/ を汚さない）。
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import orx.cli as cli
from orx.cli import app

runner = CliRunner()


@pytest.fixture()
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    runs = tmp_path / "runs"
    reports = tmp_path / "reports"
    monkeypatch.setattr(cli, "runs_root", lambda: runs)
    monkeypatch.setattr(cli, "reports_dir", lambda: reports)
    return runs, reports


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["demo", "sim", "replay", "report", "cq", "version"]:
        assert cmd in result.output


def test_demo_end_to_end(isolated_dirs: tuple[Path, Path]) -> None:
    runs, _ = isolated_dirs
    result = runner.invoke(app, ["demo", "--duration", "6.0"])
    assert result.exit_code == 0, result.output
    assert "忠実度レポート" in result.output
    assert "OK: 忠実度F1 > 0.95" in result.output
    assert (runs / "demo-seed7" / "metrics.json").exists()


def test_sim_run_and_replay_and_report(isolated_dirs: tuple[Path, Path]) -> None:
    runs, reports = isolated_dirs
    result = runner.invoke(
        app,
        ["sim", "run", "configs/world/demo_tiny.yaml", "--duration", "6.0", "--run-id", "t1"],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["replay", "t1", "--condition", "OR-no-identity"])
    assert result.exit_code == 0, result.output
    assert (runs / "t1" / "replays" / "OR-no-identity" / "metrics.json").exists()

    result = runner.invoke(app, ["report", "t1"])
    assert result.exit_code == 0, result.output
    report_md = (reports / "t1.md").read_text(encoding="utf-8")
    assert "再現情報" in report_md
    assert "OR-no-identity" in report_md  # リプレイ条件の比較表


def test_replay_missing_run_fails_actionably(isolated_dirs: tuple[Path, Path]) -> None:
    result = runner.invoke(app, ["replay", "nope"])
    assert result.exit_code == 1
    assert "見つかりません" in result.output


def test_replay_unknown_condition_fails(isolated_dirs: tuple[Path, Path]) -> None:
    runner.invoke(
        app, ["sim", "run", "configs/world/demo_tiny.yaml", "--duration", "4.0", "--run-id", "t2"]
    )
    result = runner.invoke(app, ["replay", "t2", "--condition", "bogus"])
    assert result.exit_code == 1
    assert "未知の条件" in result.output


def test_sim_run_invalid_config_fails(isolated_dirs: tuple[Path, Path], tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nbogus: 1\n", encoding="utf-8")
    result = runner.invoke(app, ["sim", "run", str(bad)])
    assert result.exit_code == 1
    assert "検証エラー" in result.output


def test_cq_command_passes(isolated_dirs: tuple[Path, Path]) -> None:
    result = runner.invoke(app, ["cq"])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
    assert "FAIL" not in result.output
