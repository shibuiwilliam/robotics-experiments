"""M-Scenario-A（Tier A 比較レポート）の統合テスト（オフライン）。"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import orx.cli as cli
from orx.cli import app
from orx.exp import scenario as scn

RUNNER = CliRunner()


def test_run_milestone_all_green(tmp_path: Path) -> None:
    _runs, milestone = scn.run_milestone(scn.TIER_A, tmp_path, progress=None)
    assert milestone["scenarios"] == ["s1", "s2", "s6"]
    for sid in scn.TIER_A:
        fal = milestone["results"][sid]["falsification"]
        assert all(fal.values()), f"{sid} falsification not all green"


def test_render_milestone_has_scope_and_rollup(tmp_path: Path) -> None:
    _runs, milestone = scn.run_milestone(scn.TIER_A, tmp_path, progress=None)
    md = scn.render_milestone("M-Scenario-A", milestone)
    assert "計測射程" in md
    assert "反証予言の総括" in md
    assert "反証総合判定: 全green ✓" in md
    for sid in ("s1", "s2", "s6"):
        assert sid in md


def test_milestone_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "runs_root", lambda: tmp_path / "runs")
    monkeypatch.setattr(cli, "reports_dir", lambda: tmp_path / "reports")
    res = RUNNER.invoke(app, ["scenario", "milestone", "M-Scenario-A"])
    assert res.exit_code == 0, res.output
    assert "全シナリオ反証 green" in res.output
    assert (tmp_path / "reports" / "milestone-M-Scenario-A.md").exists()
