"""S2 replay-identity（H-3）＋メタモルフィック（H-4）＋スタンプ（H-2）＋統合。"""

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
from orx.exp.suites.s2_allergen import generator, runner
from orx.exp.suites.s2_allergen.model import S2World
from orx.exp.suites.s2_allergen.reference import CONDITIONS, grasp_allowed_under
from orx.perception.contacts import distill_contacts

RUNNER = CliRunner()


def _canonical(result: dict) -> str:
    r = dict(result)
    r.pop("exp_id", None)
    return json.dumps(r, sort_keys=True, ensure_ascii=False, indent=2)


def _cfg() -> ScenarioExperimentConfig:
    return ScenarioExperimentConfig(
        name="s2-rep", scenario="s2", world_config=runner.WORLD,
        conditions=CONDITIONS, seeds=[201, 202, 203], duration_s=0.0,
    )


def test_replay_identity_byte_identical(tmp_path: Path) -> None:
    r1 = runner.run(_cfg(), tmp_path / "a", lambda *a: None)
    r2 = runner.run(_cfg(), tmp_path / "b", lambda *a: None)
    assert _canonical(r1) == _canonical(r2)


def test_reproducibility_stamps(tmp_path: Path) -> None:
    r = runner.run(_cfg(), tmp_path / "a", lambda *a: None)
    for key in ("config_hash", "seeds", "git_commit", "orx_version", "model_snapshot"):
        assert r.get(key), f"{key} missing"


def test_metamorphic_allergen_rename_invariant() -> None:
    """アレルゲン名を意味保存リネームしても把持可否の判定は不変（構造依拠の証拠）。"""
    world = load_config(repo_root() / "configs/world/s2_allergen.yaml", S2World)
    renamed = world.model_copy(
        update={
            "allergens": [a + "X" for a in world.allergens],
            "products": [
                p.model_copy(update={"intrinsic": [a + "X" for a in p.intrinsic]})
                for p in world.products
            ],
        }
    )
    observers = world.robots
    for seed in (201, 202, 203):
        base_ep = generator.generate_episode(world, seed)
        ren_ep = generator.generate_episode(renamed, seed)
        d_base = distill_contacts(base_ep.contacts, 0.0, SeedTree(seed).child("s2-distill").rng())
        d_ren = distill_contacts(ren_ep.contacts, 0.0, SeedTree(seed).child("s2-distill").rng())
        for c in CONDITIONS:
            base_ans = [
                grasp_allowed_under(c, base_ep, d_base, observers, q) for q in base_ep.queries
            ]
            ren_ans = [
                grasp_allowed_under(c, ren_ep, d_ren, observers, q) for q in ren_ep.queries
            ]
            assert base_ans == ren_ans, f"{c}: アレルゲンrenameで判定が変化（表層依存）"


def test_scenario_demo_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "runs_root", lambda: tmp_path / "runs")
    res = RUNNER.invoke(app, ["scenario", "demo", "s2"])
    assert res.exit_code == 0, res.output
    assert "反証テスト green" in res.output
    assert "計測射程" in res.output


def test_registered_in_list() -> None:
    res = RUNNER.invoke(app, ["scenario", "list"])
    assert res.exit_code == 0
    assert "s2" in res.output and "T9" in res.output
