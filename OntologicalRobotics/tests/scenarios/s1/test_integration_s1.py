"""S1 統合テスト: scenario demo / CLI / レポート3節 / 再現性スタンプ。完全オフライン。"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import orx.cli as cli
from orx.cli import app
from orx.exp.suites.s1_lot_recall import runner

RUNNER = CliRunner()


def test_demo_runs_and_report_has_three_sections(tmp_path: Path) -> None:
    result, report_md = runner.demo(tmp_path, lambda *a: None)
    # レポート3必須節（SCENARIOS.md §6）
    assert "## 1. 失敗予言の検証結果" in report_md
    assert "## 2. 頑健性曲線" in report_md
    assert "## 3. トークン効率" in report_md
    # 再現性スタンプ
    assert result["config_hash"]
    assert result["recall_lots"]
    assert set(result["conditions"]) == {"OR-full", "B0", "B1"}
    # 反証 green
    assert all(result["falsification"].values())


def test_scenario_list_cli() -> None:
    res = RUNNER.invoke(app, ["scenario", "list"])
    assert res.exit_code == 0
    assert "s1" in res.output and "T8" in res.output


def test_scenario_demo_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "runs_root", lambda: tmp_path / "runs")
    res = RUNNER.invoke(app, ["scenario", "demo", "s1"])
    assert res.exit_code == 0, res.output
    assert "反証テスト green" in res.output


def test_scenario_run_and_report_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs = tmp_path / "runs"
    reports = tmp_path / "reports"
    monkeypatch.setattr(cli, "runs_root", lambda: runs)
    monkeypatch.setattr(cli, "reports_dir", lambda: reports)
    # 小さめの実験コンフィグをその場で作る
    cfg = tmp_path / "s1.yaml"
    cfg.write_text(
        "name: s1-it\nscenario: s1\nworld_config: configs/world/s1_lot_recall.yaml\n"
        "conditions: [OR-full, B0, B1]\nseeds: [101, 102, 103]\nduration_s: 18.0\n"
        "claim_ttl_s: 8.0\n",
        encoding="utf-8",
    )
    res = RUNNER.invoke(app, ["scenario", "run", str(cfg)])
    assert res.exit_code == 0, res.output
    # exp ディレクトリと results.json が出来、report が生成される
    exp_dirs = list(runs.glob("scenario-s1-*"))
    assert exp_dirs and (exp_dirs[0] / "results.json").exists()
    md = list(reports.glob("scenario-s1-*.md"))
    assert md and "失敗予言" in md[0].read_text(encoding="utf-8")


def test_unknown_scenario_fails() -> None:
    res = RUNNER.invoke(app, ["scenario", "demo", "s99"])
    assert res.exit_code == 1
    assert "未知のシナリオ" in res.output
