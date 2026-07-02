"""S4 replay-identity（H-3）＋メタモルフィック（H-4）＋スタンプ（H-2）＋統合。"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import orx.cli as cli
from orx.cli import app
from orx.common.config import load_config
from orx.common.paths import repo_root
from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s4_inspection import generator, runner
from orx.exp.suites.s4_inspection.model import S4World
from orx.exp.suites.s4_inspection.reference import CONDITIONS
from orx.exp.suites.s4_inspection.scorer import score_condition

RUNNER = CliRunner()


def _canonical(result: dict) -> str:
    r = dict(result)
    r.pop("exp_id", None)
    return json.dumps(r, sort_keys=True, ensure_ascii=False, indent=2)


def _cfg() -> ScenarioExperimentConfig:
    return ScenarioExperimentConfig(
        name="s4-rep",
        scenario="s4",
        world_config=runner.WORLD,
        conditions=CONDITIONS,
        seeds=[401, 402, 403],
        duration_s=0.0,
        knob="position_noise",
        knob_values=[0.2, 0.4],
        params={"position_noise": 0.4},
    )


def test_replay_identity_byte_identical(tmp_path: Path) -> None:
    r1 = runner.run(_cfg(), tmp_path / "a", lambda *a: None)
    r2 = runner.run(_cfg(), tmp_path / "b", lambda *a: None)
    assert _canonical(r1) == _canonical(r2)


def test_reproducibility_stamps(tmp_path: Path) -> None:
    r = runner.run(_cfg(), tmp_path / "a", lambda *a: None)
    for key in ("config_hash", "seeds", "git_commit", "orx_version", "model_snapshot"):
        assert r.get(key), f"{key} missing"


def test_metamorphic_system_rename_invariant() -> None:
    """系統ラベルを意味保存リネームしても採点不変（異常→系統→SOP連鎖が構造依拠の証拠）。"""
    world = load_config(repo_root() / "configs/world/s4_inspection.yaml", S4World)
    suf = "Z"
    renamed = world.model_copy(
        update={
            "assets": [a.model_copy(update={"system": a.system + suf}) for a in world.assets],
            "sop": {k + suf: v for k, v in world.sop.items()},
        }
    )
    for seed in (401, 402, 403):
        base_ep = generator.generate_episode(world, seed, 0.4)
        ren_ep = generator.generate_episode(renamed, seed, 0.4)
        for c in CONDITIONS:
            b = score_condition(world, base_ep, c)
            m = score_condition(renamed, ren_ep, c)
            assert b.model_dump() == m.model_dump(), f"{c}: 系統renameで採点変化"


def test_scenario_demo_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "runs_root", lambda: tmp_path / "runs")
    res = RUNNER.invoke(app, ["scenario", "demo", "s4"])
    assert res.exit_code == 0, res.output
    assert "反証テスト green" in res.output
    assert "計測射程" in res.output


def test_registered_in_list() -> None:
    res = RUNNER.invoke(app, ["scenario", "list"])
    assert res.exit_code == 0
    assert "s4" in res.output and "T11" in res.output
