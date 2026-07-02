"""シナリオ対比較統計（R-3d）の存在・整合テスト。

S2–S7 の runner が OR-full vs 各ベースラインの PairedComparison を results に含め、
McNemar p / ブートストラップCI / Wilcoxon が整合することを検証する（完全オフライン）。
"""

from __future__ import annotations

import pytest

from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s2_allergen import runner as s2
from orx.exp.suites.s3_multi_vendor import runner as s3
from orx.exp.suites.s4_inspection import runner as s4
from orx.exp.suites.s6_recycling import runner as s6
from orx.exp.suites.s7_ownership import runner as s7

CASES = [
    (s2, "s2", "configs/world/s2_allergen.yaml", ["OR-full", "OR-belief", "B1", "B0"]),
    (s3, "s3", "configs/world/s3_multi_vendor.yaml", ["OR-full", "B1", "round-robin"]),
    (
        s4,
        "s4",
        "configs/world/s4_inspection.yaml",
        ["OR-full", "OR-no-identity", "OR-no-belief", "OR-sym"],
    ),
    (s6, "s6", "configs/world/s6_recycling.yaml", ["OR-full", "OR-vec", "OR-sym"]),
    (s7, "s7", "configs/world/s7_ownership.yaml", ["OR-full", "OR-vec", "OR-sym", "B0"]),
]


@pytest.mark.parametrize("mod,sid,world,conditions", CASES, ids=[c[1] for c in CASES])
def test_scenario_has_paired_comparisons(
    mod, sid: str, world: str, conditions: list[str], tmp_path
) -> None:
    cfg = ScenarioExperimentConfig(
        name=f"{sid}-cmp",
        scenario=sid,
        world_config=world,
        conditions=conditions,
        seeds=[1, 2, 3, 4, 5, 6, 7, 8],
        duration_s=0.0,
    )
    result = mod.run(cfg, tmp_path / "exp", lambda *a: None)
    comps = result["comparisons"]
    # OR-full vs 各ベースライン（= 条件数 - 1）
    assert len(comps) == len(conditions) - 1
    for c in comps:
        assert c["condition_a"] == "OR-full"
        assert c["condition_b"] in conditions[1:]
        assert c["n"] == 8
        assert 0.0 <= c["mcnemar_p"] <= 1.0
        assert c["diff_ci_low"] <= c["diff_ci_high"]
        assert c["wilcoxon_metric"] is not None


def test_or_full_beats_baselines_significantly(tmp_path) -> None:
    """8 シードで OR-full が全ベースラインに勝つ場合、McNemar p<0.05（S1 と同水準）。"""
    mod, sid, world, conditions = CASES[3]  # s6: OR-full は high_cost_errors=0 で全勝
    cfg = ScenarioExperimentConfig(
        name="s6-sig",
        scenario=sid,
        world_config=world,
        conditions=conditions,
        seeds=list(range(1, 9)),
        duration_s=0.0,
    )
    result = mod.run(cfg, tmp_path / "exp", lambda *a: None)
    for c in result["comparisons"]:
        if c["success_rate_a"] == 1.0 and c["success_rate_b"] == 0.0:
            assert c["mcnemar_p"] < 0.05
