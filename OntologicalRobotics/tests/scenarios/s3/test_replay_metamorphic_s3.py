"""S3 replay-identity（H-3）＋メタモルフィック（H-4）＋スタンプ（H-2）＋統合。"""

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
from orx.exp.suites.s3_multi_vendor import runner
from orx.exp.suites.s3_multi_vendor.model import S3World
from orx.exp.suites.s3_multi_vendor.reference import CONDITIONS
from orx.exp.suites.s3_multi_vendor.scorer import simulate_condition

RUNNER = CliRunner()


def _canonical(result: dict) -> str:
    r = dict(result)
    r.pop("exp_id", None)
    return json.dumps(r, sort_keys=True, ensure_ascii=False, indent=2)


def _cfg() -> ScenarioExperimentConfig:
    return ScenarioExperimentConfig(
        name="s3-rep",
        scenario="s3",
        world_config=runner.WORLD,
        conditions=CONDITIONS,
        seeds=[301, 302, 303],
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


def test_metamorphic_material_rename_invariant() -> None:
    """素材タグを意味保存リネームしても採点（正答・完遂・台帳）は不変（構造依拠の証拠）。"""
    world = load_config(repo_root() / "configs/world/s3_multi_vendor.yaml", S3World)
    suf = "Z"

    def rmat(m: str) -> str:
        return m if m == "default" else m + suf

    renamed = world.model_copy(
        update={
            "products": {
                k: p.model_copy(update={"material": rmat(p.material)})
                for k, p in world.products.items()
            },
            "machines": [
                m.model_copy(
                    update={
                        "true_material_success": {
                            rmat(k): v for k, v in m.true_material_success.items()
                        }
                    }
                )
                for m in world.machines
            ],
        }
    )
    for seed in (301, 302, 303):
        for c in CONDITIONS:
            base = simulate_condition(world, c, SeedTree(seed).child("s3").child(c).rng())
            renm = simulate_condition(renamed, c, SeedTree(seed).child("s3").child(c).rng())
            assert base.model_dump() == renm.model_dump(), f"{c}: 素材renameで採点変化"


def test_scenario_demo_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "runs_root", lambda: tmp_path / "runs")
    res = RUNNER.invoke(app, ["scenario", "demo", "s3"])
    assert res.exit_code == 0, res.output
    assert "反証テスト green" in res.output
    assert "計測射程" in res.output


def test_registered_in_list() -> None:
    res = RUNNER.invoke(app, ["scenario", "list"])
    assert res.exit_code == 0
    assert "s3" in res.output and "T10" in res.output
