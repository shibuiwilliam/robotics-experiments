"""S1 シナリオ run の replay-identity（H-3）＋再現性スタンプ（H-2）。

同一コンフィグ・同一シードで2回評価し、exp_id（ディレクトリ名）以外の結果が
正準JSONでバイト一致することを確認する（決定的リファレンスソルバ＝完全再現）。
"""

import json
from pathlib import Path

import pytest

from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s1_lot_recall import runner


def _canonical(result: dict) -> str:
    r = dict(result)
    r.pop("exp_id", None)  # ディレクトリ名は実行毎に異なるため除外
    return json.dumps(r, sort_keys=True, ensure_ascii=False, indent=2)


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, dict]:
    cfg = ScenarioExperimentConfig(
        name="s1-replay",
        scenario="s1",
        world_config=runner.WORLD,
        conditions=runner.CONDITIONS,
        seeds=[101, 102, 103],
        duration_s=18.0,
        claim_ttl_s=8.0,
    )
    root = tmp_path_factory.mktemp("s1replay")
    r1 = runner.run(cfg, root / "a", lambda *a: None)
    r2 = runner.run(cfg, root / "b", lambda *a: None)
    return r1, r2


def test_replay_identity_byte_identical(two_runs: tuple[dict, dict]) -> None:
    r1, r2 = two_runs
    assert _canonical(r1) == _canonical(r2)


def test_reproducibility_stamps_present(two_runs: tuple[dict, dict]) -> None:
    r1, _ = two_runs
    # DoD: config hash, seed, git commit, model snapshot を全シナリオ出力に焼き込む
    assert r1["config_hash"]
    assert r1["seeds"] == [101, 102, 103]
    assert r1["git_commit"] and r1["git_commit"] != ""
    assert r1["orx_version"]
    assert r1["model_snapshot"]


def test_results_json_carries_stamps(tmp_path: Path) -> None:
    """run_experiment 経由の results.json にもスタンプが残る。"""
    from orx.exp import scenario as scn

    cfg = ScenarioExperimentConfig(
        name="s1-stamp", scenario="s1", world_config=runner.WORLD,
        conditions=runner.CONDITIONS, seeds=[101, 102], duration_s=18.0, claim_ttl_s=8.0,
    )
    exp_dir, _ = scn.run_experiment(cfg, tmp_path, progress=None)
    data = json.loads((exp_dir / "results.json").read_text(encoding="utf-8"))
    for key in ("config_hash", "seeds", "git_commit", "orx_version", "model_snapshot"):
        assert key in data and data[key], f"results.json に {key} が無い"
