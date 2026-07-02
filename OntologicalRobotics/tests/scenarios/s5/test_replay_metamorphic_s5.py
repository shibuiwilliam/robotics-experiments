"""S5 replay-identity（H-3）＋メタモルフィック（H-4）＋スタンプ（H-2）＋統合。"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import orx.cli as cli
from orx.cli import app
from orx.common.config import load_config
from orx.common.paths import repo_root
from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s5_hospital import runner
from orx.exp.suites.s5_hospital.model import S5World
from orx.exp.suites.s5_hospital.reference import CONDITIONS
from orx.exp.suites.s5_hospital.scorer import score_condition

RUNNER = CliRunner()


def _canonical(result: dict) -> str:
    r = dict(result)
    r.pop("exp_id", None)
    return json.dumps(r, sort_keys=True, ensure_ascii=False, indent=2)


def _cfg() -> ScenarioExperimentConfig:
    return ScenarioExperimentConfig(
        name="s5-rep",
        scenario="s5",
        world_config=runner.WORLD,
        conditions=CONDITIONS,
        seeds=[501],
        duration_s=0.0,
    )


def test_replay_identity_byte_identical(tmp_path: Path) -> None:
    r1 = runner.run(_cfg(), tmp_path / "a", lambda *a: None)
    r2 = runner.run(_cfg(), tmp_path / "b", lambda *a: None)
    assert _canonical(r1) == _canonical(r2)


def test_reproducibility_stamps(tmp_path: Path) -> None:
    r = runner.run(_cfg(), tmp_path / "a", lambda *a: None)
    for key in ("config_hash", "seeds", "git_commit", "orx_version", "model_snapshot"):
        assert r.get(key), f"{key} missing"


def test_metamorphic_zoneclass_rename_invariant() -> None:
    """区画分類ラベルを意味保存リネームしても採点不変（規範が構造依拠の証拠）。

    ノード名は不変（経路は同一）。区画**分類**と禁止規範の分類ラベルのみ一貫リネーム。
    """
    world = load_config(repo_root() / "configs/world/s5_hospital.yaml", S5World)
    renamed = world.model_copy(
        update={
            "zone_class": {z: c + "Z" for z, c in world.zone_class.items()},
            "norms": [
                n.model_copy(update={"forbidden_zone_class": n.forbidden_zone_class + "Z"})
                for n in world.norms
            ],
        }
    )
    for c in CONDITIONS:
        b = score_condition(world, c)
        m = score_condition(renamed, c)
        assert b.model_dump() == m.model_dump(), f"{c}: 区画分類renameで採点変化"


def test_scenario_demo_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "runs_root", lambda: tmp_path / "runs")
    res = RUNNER.invoke(app, ["scenario", "demo", "s5"])
    assert res.exit_code == 0, res.output
    assert "反証テスト green" in res.output
    assert "計測射程" in res.output


def test_registered_in_list() -> None:
    res = RUNNER.invoke(app, ["scenario", "list"])
    assert res.exit_code == 0
    assert "s5" in res.output and "T12" in res.output
