"""S6 replay-identity（H-3）＋メタモルフィック（H-4）＋スタンプ（H-2）＋統合。"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import orx.cli as cli
from orx.cli import app
from orx.common.config import load_config
from orx.common.paths import repo_root
from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s6_recycling import generator, runner
from orx.exp.suites.s6_recycling.grounding import class_prototypes
from orx.exp.suites.s6_recycling.model import S6World
from orx.exp.suites.s6_recycling.reference import CONDITIONS, assign_lane
from orx.exp.suites.s6_recycling.scorer import score

RUNNER = CliRunner()


def _canonical(result: dict) -> str:
    r = dict(result)
    r.pop("exp_id", None)
    return json.dumps(r, sort_keys=True, ensure_ascii=False, indent=2)


def _cfg() -> ScenarioExperimentConfig:
    return ScenarioExperimentConfig(
        name="s6-rep", scenario="s6", world_config=runner.WORLD, conditions=CONDITIONS,
        seeds=[601, 602, 603], duration_s=0.0, knob="visual_noise", knob_values=[0.1, 1.0],
        params={"primary_sigma": 0.2, "confidence_threshold": 0.15, "high_cost_threshold": 50.0},
    )


def test_replay_identity_byte_identical(tmp_path: Path) -> None:
    r1 = runner.run(_cfg(), tmp_path / "a", lambda *a: None)
    r2 = runner.run(_cfg(), tmp_path / "b", lambda *a: None)
    assert _canonical(r1) == _canonical(r2)


def test_reproducibility_stamps(tmp_path: Path) -> None:
    r = runner.run(_cfg(), tmp_path / "a", lambda *a: None)
    for key in ("config_hash", "seeds", "git_commit", "orx_version", "model_snapshot"):
        assert r.get(key), f"{key} missing"


def test_metamorphic_lane_rename_invariant() -> None:
    """レーン名を意味保存リネームしても採点（コスト・正答）は不変（構造依拠の証拠）。

    クラス（プロトタイプ）は固定し、レーンの**ラベル**のみ一貫リネームする。
    """
    world = load_config(repo_root() / "configs/world/s6_recycling.yaml", S6World)
    suffix = "Z"

    def ren(lane: str) -> str:
        return lane + suffix

    renamed = world.model_copy(
        update={
            "lanes": [ren(x) for x in world.lanes],
            "default_lane": ren(world.default_lane),
            "disposal_route": {c: ren(v) for c, v in world.disposal_route.items()},
            "cost": {tc: {ren(la): v for la, v in m.items()} for tc, m in world.cost.items()},
        }
    )
    protos = class_prototypes(world.classes, world.embedding_dim)  # クラス不変→共有
    for seed in (601, 602, 603):
        base_ep = generator.generate_episode(world, seed, 0.2)
        ren_ep = generator.generate_episode(renamed, seed, 0.2)
        for c in CONDITIONS:
            base = score(
                [(o, *assign_lane(c, o, world, protos, 0.15)) for o in base_ep.objects],
                world, 50.0,
            )
            renm = score(
                [(o, *assign_lane(c, o, renamed, protos, 0.15)) for o in ren_ep.objects],
                renamed, 50.0,
            )
            assert base.model_dump() == renm.model_dump(), f"{c}: レーンrenameで採点変化"


def test_scenario_demo_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "runs_root", lambda: tmp_path / "runs")
    res = RUNNER.invoke(app, ["scenario", "demo", "s6"])
    assert res.exit_code == 0, res.output
    assert "反証テスト green" in res.output
    assert "計測射程" in res.output


def test_registered_in_list() -> None:
    res = RUNNER.invoke(app, ["scenario", "list"])
    assert res.exit_code == 0
    assert "s6" in res.output and "T13" in res.output
