"""S5 反証テスト（規範層・H5/H6）— 4条件が構造的に異なる失敗をし OR-full が最良。

完全オフライン・決定的。no-rigging: 規範/来歴の各機構はシグネチャで強制（ADR-014）。
"""

import pytest

from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s5_hospital import runner


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    cfg = ScenarioExperimentConfig(
        name="s5-fal",
        scenario="s5",
        world_config=runner.WORLD,
        conditions=runner.CONDITIONS,
        seeds=[501],
        duration_s=0.0,
    )
    return runner.run(cfg, tmp_path_factory.mktemp("s5") / "exp", lambda *a: None)


def test_no_normative_violates(result: dict) -> None:
    """規範層なしは最短経路で禁止区画を通過（違反）。"""
    assert result["per_condition"]["OR-no-normative"]["violations"] > 0


def test_no_prov_audit_incomplete(result: dict) -> None:
    """来歴なしは custody を再構成できず監査クエリに完全回答できない。"""
    assert result["per_condition"]["OR-no-prov"]["audit_completeness"] < 1.0


def test_b1_violates_and_unauditable(result: dict) -> None:
    """B1 は規範も来歴も無く 違反かつ監査不能（最悪）。"""
    b1 = result["per_condition"]["B1"]
    assert b1["violations"] > 0
    assert b1["audit_completeness"] < 1.0


def test_or_full_compliant_auditable(result: dict) -> None:
    """OR-full は違反0かつ監査完全（規範コストは定量化して許容）。"""
    full = result["per_condition"]["OR-full"]
    assert full["violations"] == 0
    assert full["audit_completeness"] == 1.0
    assert full["normative_cost"] > 0.0  # 効率劣化を定量化


def test_normative_cost_tradeoff(result: dict) -> None:
    """規範遵守は経路を延ばす（規範なしより規範コストが高い）が違反0を達成。"""
    pc = result["per_condition"]
    assert pc["OR-full"]["normative_cost"] >= pc["OR-no-normative"]["normative_cost"]


def test_robustness_or_full_audit_beats_baselines_across_sweep(result: dict) -> None:
    """前面化した頑健性指標(R-1C): custody 欠落率 掃引の**全域**で OR-full の監査完全性が
    各条件以上。S5 は決定的・seed 非依存で対比較 p 値を持たないため、この掃引曲線が主指標
    （IMPROVEMENT.md §1-C: 頑健性掃引の前面化）。回帰でこの優位が崩れないことを固定する。"""
    ac = result["robustness"]["audit_completeness"]
    full = ac["OR-full"]
    assert len(full) >= 3  # 掃引点が存在
    for b in ("OR-no-normative", "OR-no-prov", "B1"):
        assert all(f >= ac[b][i] - 1e-9 for i, f in enumerate(full))


def test_all_predictions_pass(result: dict) -> None:
    assert result["falsification"] == {
        "no_normative_violates": True,
        "no_prov_audit_incomplete": True,
        "b1_violates_and_unauditable": True,
        "or_full_compliant_auditable": True,
    }
