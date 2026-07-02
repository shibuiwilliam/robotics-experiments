"""S7 反証テスト（H2/H4/H6）— 4条件が構造的に異なる失敗をし OR-full が最良（誤配送0）。

完全オフライン・決定的。no-rigging: 各条件は許される情報のみで解く（ADR-014）。
"""

import pytest

from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s7_ownership import runner


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    cfg = ScenarioExperimentConfig(
        name="s7-fal",
        scenario="s7",
        world_config=runner.WORLD,
        conditions=runner.CONDITIONS,
        seeds=[701, 702, 703, 704, 705, 706],
        duration_s=0.0,
        knob="lookalike_sep",
        knob_values=[1.5, 1.0, 0.6, 0.3],
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


def test_hardcase_crowded_zone_or_full_zero_misdelivery(result: dict) -> None:
    """ハードケース(R-1C): 最混雑ゾーン(4人)でも OR-full は署名＋確認(X5)で誤配送0、
    OR-vec/B0 は owner非依存で誤配送多数。ゾーン+記号が最も無力な4-way曖昧で H4 の価値を示す。"""
    from collections import Counter

    from orx.common.config import load_config
    from orx.common.paths import repo_root
    from orx.exp.suites.s7_ownership.model import S7World

    world = load_config(repo_root() / runner.WORLD, S7World)
    crowd = Counter(world.resident_zone.values())
    assert max(crowd.values()) >= 4  # 4人ゾーン（ハードケース）の存在
    pc = result["per_condition"]
    assert pc["OR-full"]["misdelivery_rate"] == 0.0
    assert pc["OR-vec"]["misdelivery_rate"] > 0.5


def test_success_rate_powered_vs_b0_at_production_seeds() -> None:
    """§2 検出力: 本番シード数（20）では自動成功率の OR-full vs B0 が連続指標でも有意（p<0.05）。

    主操作点 sep=0.6 は OR-full の確認委譲が多く、8 seed では Wilcoxon p≈0.0625（不確定）だった。
    効果は全 seed 数で一貫（OR-full≈2×B0）なため 20 seed で適切に検出力を与える。安全側（誤配送0）の
    McNemar は更に有意。これを回帰固定し、シード数削減や効果消失で再び不確定化しないようにする。"""
    from pathlib import Path

    from orx.exp import scenario as scn

    cfg = scn.load_scenario_experiment(Path("configs/experiments/s7_ownership.yaml"))
    assert len(cfg.seeds) >= 16  # 検出力確保のための本番シード数
    res = runner.run(cfg, Path("/tmp"), lambda *a: None)
    cmp_b0 = next(c for c in res["comparisons"] if c["condition_b"] == "B0")
    assert cmp_b0["wilcoxon_p"] < 0.05  # 自動成功率の優位が連続指標で有意
    assert cmp_b0["mcnemar_p"] < 0.05  # 安全（誤配送0）の優位も有意
    assert res["per_condition"]["OR-full"]["misdelivery_rate"] == 0.0  # 誤配送0 は不変


def test_all_predictions_pass(result: dict) -> None:
    assert result["falsification"] == {
        "B0_no_ownership_path": True,
        "OR_vec_lookalike_misdelivery": True,
        "OR_sym_cannot_disambiguate": True,
        "OR_full_no_misdelivery": True,
    }
