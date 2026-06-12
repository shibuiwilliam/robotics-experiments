"""T1実験の統合テスト（縮小版・完全オフライン）。

P1完了基準の機械検証: OR-full が OR-no-identity をタスク成功で上回り、
同一性F1の差が大きいこと。本実験（20シード）はCLIで実行する。
"""

from pathlib import Path

import pytest

from orx.exp.runner import ExperimentResult, load_experiment, run_experiment

EXP_CONFIG = Path("configs/experiments/t1_identity.yaml")


@pytest.fixture(scope="module")
def mini_result(tmp_path_factory: pytest.TempPathFactory) -> ExperimentResult:
    config = load_experiment(EXP_CONFIG)
    config = config.model_copy(update={"seeds": [101, 103, 105, 108]})
    runs_root = tmp_path_factory.mktemp("t1_runs")
    _, result = run_experiment(config, runs_root)
    return result


def test_or_full_beats_ablation(mini_result: ExperimentResult) -> None:
    assert mini_result.success_rates["OR-full"] > mini_result.success_rates["OR-no-identity"]
    assert mini_result.identity_f1_means["OR-full"] > 0.9
    assert mini_result.identity_f1_means["OR-no-identity"] < 0.1


def test_paired_episodes_and_stats_present(mini_result: ExperimentResult) -> None:
    # 全シード×全条件のエピソード行が揃う（対応のある比較の前提）
    assert len(mini_result.episodes) == len(mini_result.seeds) * len(mini_result.conditions)
    assert mini_result.comparisons
    cmp = mini_result.comparisons[0]
    assert cmp.condition_a == "OR-full"
    assert 0.0 <= cmp.mcnemar_p <= 1.0
    assert cmp.wilcoxon_p is not None


def test_experiment_rejects_bad_config(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: x\ntask: t1\nworld_config: configs/world/t1_handoff.yaml\n"
        "conditions: [OR-full]\nseeds: [1]\nduration_s: 5.0\n"
        "t1: {target_pool: [b1], decoy_pool: [n1]}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="2つ以上"):
        load_experiment(bad)
