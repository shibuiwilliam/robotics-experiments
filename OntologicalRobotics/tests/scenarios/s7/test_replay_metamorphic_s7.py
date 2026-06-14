"""S7 replay-identity（H-3）＋メタモルフィック（H-4）＋スタンプ（H-2）＋統合。"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import orx.cli as cli
from orx.cli import app
from orx.common.config import load_config
from orx.common.paths import repo_root
from orx.common.seeding import SeedTree
from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s7_ownership import generator, runner
from orx.exp.suites.s7_ownership.model import S7World
from orx.exp.suites.s7_ownership.reference import CONDITIONS
from orx.exp.suites.s7_ownership.scorer import score_condition

RUNNER = CliRunner()


def _canonical(result: dict) -> str:
    r = dict(result)
    r.pop("exp_id", None)
    return json.dumps(r, sort_keys=True, ensure_ascii=False, indent=2)


def _cfg() -> ScenarioExperimentConfig:
    return ScenarioExperimentConfig(
        name="s7-rep", scenario="s7", world_config=runner.WORLD, conditions=CONDITIONS,
        seeds=[701, 702, 703], duration_s=0.0, knob="lookalike_sep", knob_values=[1.0, 0.6],
        params={"primary_sep": 0.6},
    )


def test_replay_identity_byte_identical(tmp_path: Path) -> None:
    r1 = runner.run(_cfg(), tmp_path / "a", lambda *a: None)
    r2 = runner.run(_cfg(), tmp_path / "b", lambda *a: None)
    assert _canonical(r1) == _canonical(r2)


def test_reproducibility_stamps(tmp_path: Path) -> None:
    r = runner.run(_cfg(), tmp_path / "a", lambda *a: None)
    for key in ("config_hash", "seeds", "git_commit", "orx_version", "model_snapshot"):
        assert r.get(key), f"{key} missing"


def test_metamorphic_zone_rename_invariant() -> None:
    """ゾーン名を意味保存リネームしても採点は不変（時空間チャネルが構造依拠の証拠）。

    所有者署名はリネームに不変（resident 名で seed）。ゾーンの**ラベル**のみ一貫リネーム。
    """
    world = load_config(repo_root() / "configs/world/s7_ownership.yaml", S7World)
    renamed = world.model_copy(
        update={"resident_zone": {r: z + "Z" for r, z in world.resident_zone.items()}}
    )
    for seed in (701, 702, 703):
        base_ep = generator.generate_episode(world, seed, 0.6)
        ren_ep = generator.generate_episode(renamed, seed, 0.6)
        for c in CONDITIONS:
            b = score_condition(world, base_ep, c, SeedTree(seed).child("s7").child(c).rng())
            m = score_condition(renamed, ren_ep, c, SeedTree(seed).child("s7").child(c).rng())
            assert b.model_dump() == m.model_dump(), f"{c}: ゾーンrenameで採点変化"


def test_scenario_demo_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "runs_root", lambda: tmp_path / "runs")
    res = RUNNER.invoke(app, ["scenario", "demo", "s7"])
    assert res.exit_code == 0, res.output
    assert "反証テスト green" in res.output
    assert "計測射程" in res.output


def test_registered_in_list() -> None:
    res = RUNNER.invoke(app, ["scenario", "list"])
    assert res.exit_code == 0
    assert "s7" in res.output and "T14" in res.output
