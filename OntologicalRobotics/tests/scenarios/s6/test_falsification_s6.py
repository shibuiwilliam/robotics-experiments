"""S6 反証テスト（H4 双対表現）— 3条件が構造的に異なる失敗をし OR-full が最良。

完全オフライン・決定的。no-rigging: 各条件は許される表現のみで解く（ADR-014/017）。
"""

import pytest

from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s6_recycling import runner


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    cfg = ScenarioExperimentConfig(
        name="s6-fal",
        scenario="s6",
        world_config=runner.WORLD,
        conditions=runner.CONDITIONS,
        seeds=[601, 602, 603, 604, 605, 606],
        duration_s=0.0,
        knob="visual_noise",
        knob_values=[0.1, 0.5, 1.0, 2.0],
        params={"primary_sigma": 0.2, "confidence_threshold": 0.15, "high_cost_threshold": 50.0},
    )
    return runner.run(cfg, tmp_path_factory.mktemp("s6") / "exp", lambda *a: None)


def test_or_sym_throughput_collapse(result: dict) -> None:
    """OR-sym は記号ID無しで接地不能 → 全件委譲。"""
    s = result["per_condition"]["OR-sym"]
    assert s["throughput"] == 0.0
    assert s["escalation_rate"] == 1.0


def test_or_vec_high_cost_misroute(result: dict) -> None:
    """OR-vec は規制推論が無く高コスト誤り（電池の誤レーン）。"""
    v = result["per_condition"]["OR-vec"]
    assert v["high_cost_errors"] > 0


def test_or_full_lowest_cost_no_high_cost(result: dict) -> None:
    """受入: コスト加重で OR-full が両アブレーションを上回り、高コスト誤り0。"""
    pc = result["per_condition"]
    assert pc["OR-full"]["mean_cost"] < pc["OR-vec"]["mean_cost"]
    assert pc["OR-full"]["mean_cost"] < pc["OR-sym"]["mean_cost"]
    assert pc["OR-full"]["high_cost_errors"] == 0


def test_all_predictions_pass(result: dict) -> None:
    assert result["falsification"] == {
        "OR_sym_throughput_collapse": True,
        "OR_vec_high_cost_misroute": True,
        "OR_full_lowest_cost": True,
    }


def test_robustness_or_full_beats_vec_across_sweep(result: dict) -> None:
    """視覚ノイズ掃引全域で OR-full のコスト ≤ OR-vec（双対表現の優位）。"""
    rob = result["robustness"]
    full = rob["mean_cost"]["OR-full"]
    vec = rob["mean_cost"]["OR-vec"]
    assert all(f <= v + 1e-9 for f, v in zip(full, vec, strict=True))
    assert "brier" in rob  # 較正曲線の報告
