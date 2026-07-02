"""S2 反証テスト（科学的中核）— 4条件が構造的に異なる失敗をし OR-full が成功する。

完全オフライン・決定的。no-rigging: 各ソルバはその条件が許す情報のみで解く（ADR-014）。
"""

import pytest

from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s2_allergen import runner


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    cfg = ScenarioExperimentConfig(
        name="s2-fal",
        scenario="s2",
        world_config=runner.WORLD,
        conditions=runner.CONDITIONS,
        seeds=[201, 202, 203, 204, 205, 206],
        duration_s=0.0,
    )
    return runner.run(cfg, tmp_path_factory.mktemp("s2") / "exp", lambda *a: None)


def test_or_full_correct_noise0(result: dict) -> None:
    """受入: ノイズ0で汚染集合F1=1.0・違反0。"""
    orf = result["per_condition"]["OR-full"]
    assert orf["contamination_f1"] == 1.0
    assert orf["safety_violations"] == 0
    assert orf["over_conservative"] == 0
    assert orf["accuracy"] == 1.0


def test_b0_no_history_false_negative(result: dict) -> None:
    assert result["per_condition"]["B0"]["safety_violations"] > 0
    assert result["per_condition"]["B0"]["contamination_f1"] < 1.0


def test_b1_cross_robot_miss(result: dict) -> None:
    """B1 は横断融合が無く越境連鎖を取りこぼす（安全違反）。"""
    assert result["per_condition"]["B1"]["safety_violations"] > 0
    assert result["per_condition"]["B1"]["contamination_f1"] < 1.0


def test_or_belief_overconservative(result: dict) -> None:
    """OR−belief は洗浄リセットを無視し許可把持を拒否（偽陽性）。安全違反は出さない。"""
    b = result["per_condition"]["OR-belief"]
    assert b["over_conservative"] > 0
    assert b["safety_violations"] == 0  # 過保守であって危険ではない


def test_all_predictions_pass(result: dict) -> None:
    assert result["falsification"] == {
        "B0_no_history_false_negative": True,
        "B1_cross_robot_miss": True,
        "OR_belief_overconservative": True,
        "OR_full_correct": True,
    }


def test_robustness_separation(result: dict) -> None:
    """見落とし率掃引で OR-full ≥ OR−belief（曲線が分離）。"""
    rob = result["robustness"]
    full = rob["accuracy"]["OR-full"]
    belief = rob["accuracy"]["OR-belief"]
    assert full[0] > belief[0]  # ノイズ0で既に分離（OR-belief は過保守）
    assert all(f >= b - 1e-9 for f, b in zip(full, belief, strict=True))
