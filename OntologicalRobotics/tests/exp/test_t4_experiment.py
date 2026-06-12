"""T4 劣化掃引の統合テスト（縮小版・オフライン）。

P4完了基準: 掃引曲線で OR-full と OR−belief の乖離領域を特定できること。
縮小版ではノブ2点×2シードで「劣化時に OR-full ≥ OR−belief」を確認する。
"""

from pathlib import Path

import pytest

from orx.exp.runner import T4ExperimentResult, load_experiment, run_experiment

EXP_CONFIG = Path("configs/experiments/t4_degradation_sweep.yaml")


@pytest.fixture(scope="module")
def mini_result(tmp_path_factory: pytest.TempPathFactory) -> T4ExperimentResult:
    config = load_experiment(EXP_CONFIG)
    config = config.model_copy(update={"seeds": [501, 502]})
    assert config.t4 is not None
    config = config.model_copy(
        update={"t4": config.t4.model_copy(update={"values": [0.0, 0.3]})}
    )
    _, result = run_experiment(config, tmp_path_factory.mktemp("t4"))
    assert isinstance(result, T4ExperimentResult)
    return result


def test_clean_world_no_divergence(mini_result: T4ExperimentResult) -> None:
    """ノブ0では両条件の忠実度が一致する（H5はクリーン環境では効かない）。"""
    f_full = mini_result.fidelity_curves["OR-full"][0]
    f_nb = mini_result.fidelity_curves["OR-no-belief"][0]
    assert abs(f_full - f_nb) < 0.01


def test_degraded_world_or_full_wins(mini_result: T4ExperimentResult) -> None:
    """矛盾観測下では信念調停が忠実度を守る。"""
    f_full = mini_result.fidelity_curves["OR-full"][1]
    f_nb = mini_result.fidelity_curves["OR-no-belief"][1]
    assert f_full > f_nb


def test_monotone_degradation(mini_result: T4ExperimentResult) -> None:
    """ノブを上げると忠実度は下がる（掃引が機能している）。"""
    for c in mini_result.conditions:
        curve = mini_result.fidelity_curves[c]
        assert curve[0] > curve[-1]


def test_unknown_knob_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: x\ntask: t4\nworld_config: configs/world/t2_warehouse.yaml\n"
        "conditions: [OR-full, OR-no-belief]\nseeds: [1]\nduration_s: 5.0\n"
        "t4: {knob: bogus_knob, values: [0.0]}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="未知の劣化ノブ"):
        load_experiment(bad)
