"""S3 反証テスト（H1/H3）— 3条件が構造的に異なる失敗をし OR-full が最良＋故障回復。

完全オフライン・決定的。no-rigging: 各条件は許される情報のみで解く（ADR-014）。
"""

import pytest

from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s3_multi_vendor import runner


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    cfg = ScenarioExperimentConfig(
        name="s3-fal",
        scenario="s3",
        world_config=runner.WORLD,
        conditions=runner.CONDITIONS,
        seeds=[301, 302, 303, 304, 305, 306],
        duration_s=0.0,
    )
    return runner.run(cfg, tmp_path_factory.mktemp("s3") / "exp", lambda *a: None)


def test_round_robin_capability_blind(result: dict) -> None:
    """能力無視は実現可能性を外し期待完遂が OR-full に劣る（H3帰無1）。"""
    pc = result["per_condition"]
    assert pc["round-robin"]["overall_throughput"] < pc["OR-full"]["overall_throughput"]


def test_b1_cross_vendor_fail(result: dict) -> None:
    """B1 は共通オントロジー無しで段取り替え後に語彙横断照合できず割当不能。"""
    pc = result["per_condition"]
    assert pc["B1"]["setup_accuracy"] == 0.0
    assert pc["OR-full"]["setup_accuracy"] >= 0.95


def test_or_full_best_and_recovers(result: dict) -> None:
    """OR-full は全体最良かつ故障後に縮退再割当で回復（最終正答=1）。"""
    pc = result["per_condition"]
    assert pc["OR-full"]["overall_throughput"] > pc["B1"]["overall_throughput"]
    assert pc["OR-full"]["overall_throughput"] > pc["round-robin"]["overall_throughput"]
    assert pc["OR-full"]["final_accuracy"] >= 0.95


def test_calibration_tracks_fault(result: dict) -> None:
    """能力台帳が経年劣化を検知し故障機体の推定が追従低下（T6）。"""
    cal = result["calibration"]
    assert cal["fault_estimate_onset"] - cal["fault_estimate_end"] > 0.1


def test_onboarding_hub_reduces_manual_lines(result: dict) -> None:
    """H1: ハブ写像で新ベンダー統合の手修正行数が B1 より少ない。"""
    o = result["onboarding"]
    assert o["or_full_manual_lines"] < o["b1_manual_lines"]
    assert o["auto_mapped"] > 0


def test_robustness_or_full_accuracy_beats_baselines_across_sweep(result: dict) -> None:
    """前面化した頑健性指標(R-1C): 故障劣化係数 掃引の**全域**で OR-full の全体正答率が
    各条件以上（能力台帳の追従で縮退再割当し優位を保つ）。掃引曲線を回帰固定する
    （IMPROVEMENT.md §1-C: 決定的世界は頑健性掃引を主指標に前面化）。"""
    acc = result["robustness"]["overall_accuracy"]
    full = acc["OR-full"]
    assert len(full) >= 3  # 掃引点が存在
    for b in ("B1", "round-robin"):
        assert all(f >= acc[b][i] - 1e-9 for i, f in enumerate(full))


def test_all_predictions_pass(result: dict) -> None:
    assert result["falsification"] == {
        "round_robin_capability_blind": True,
        "B1_cross_vendor_fail": True,
        "OR_full_best_and_recovers": True,
        "calibration_tracks_fault": True,
        "B1_onboarding_costly": True,
    }
