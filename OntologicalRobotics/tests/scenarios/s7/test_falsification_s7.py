"""S7 反証テスト（H2/H4/H6）— 4条件が構造的に異なる失敗をし OR-full が最良（誤配送0）。

完全オフライン・決定的。no-rigging: 各条件は許される情報のみで解く（ADR-014）。
"""

import pytest

from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s7_ownership import runner


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    cfg = ScenarioExperimentConfig(
        name="s7-fal", scenario="s7", world_config=runner.WORLD,
        conditions=runner.CONDITIONS, seeds=[701, 702, 703, 704, 705, 706],
        duration_s=0.0, knob="lookalike_sep", knob_values=[1.5, 1.0, 0.6, 0.3],
        params={"primary_sep": 0.6},
    )
    return runner.run(cfg, tmp_path_factory.mktemp("s7") / "exp", lambda *a: None)


def test_b0_no_ownership_path(result: dict) -> None:
    """B0 は所有情報経路が無く誤配送多数（H6）。"""
    assert result["per_condition"]["B0"]["misdelivery_rate"] > 0.5


def test_or_vec_lookalike_misdelivery(result: dict) -> None:
    """OR-vec は所有関係を扱えず瓜二つで誤配送（H4）。"""
    assert result["per_condition"]["OR-vec"]["misdelivery_rate"] > 0.0


def test_or_sym_cannot_disambiguate(result: dict) -> None:
    """OR-sym は視覚署名が無くID無し瓜二つを判別できず確認多数・自動成功低（H4）。"""
    sym = result["per_condition"]["OR-sym"]
    assert sym["escalation_rate"] > 0.5
    assert sym["success_rate"] < result["per_condition"]["OR-full"]["success_rate"]


def test_or_full_no_misdelivery_best(result: dict) -> None:
    """OR-full は三系統融合＋確認(X5)で誤配送0かつ最高自動成功。"""
    pc = result["per_condition"]
    assert pc["OR-full"]["misdelivery_rate"] == 0.0
    assert pc["OR-full"]["success_rate"] >= pc["OR-vec"]["success_rate"]
    assert pc["OR-full"]["success_rate"] >= pc["OR-sym"]["success_rate"]


def test_robustness_or_full_beats_vec_across_sweep(result: dict) -> None:
    """look-alike 分離 掃引の全域で OR-full の誤配送 ≤ OR-vec（三系統融合の優位）。"""
    rob = result["robustness"]
    full = rob["misdelivery_rate"]["OR-full"]
    vec = rob["misdelivery_rate"]["OR-vec"]
    assert all(f <= v + 1e-9 for f, v in zip(full, vec, strict=True))
    assert all(f == 0.0 for f in full)  # 確認(X5)で全域 誤配送0


def test_all_predictions_pass(result: dict) -> None:
    assert result["falsification"] == {
        "B0_no_ownership_path": True,
        "OR_vec_lookalike_misdelivery": True,
        "OR_sym_cannot_disambiguate": True,
        "OR_full_no_misdelivery": True,
    }
