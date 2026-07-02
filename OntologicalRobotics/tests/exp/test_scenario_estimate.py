"""シナリオ agent(live) コスト概算（estimate_scenario）の単体テスト（実APIなし）。"""

from __future__ import annotations

from pathlib import Path

from orx.common.paths import repo_root
from orx.exp.cost import estimate_scenario
from orx.exp.scenario import load_scenario_experiment


def test_s8_live_estimate_counts_all_agent_conditions() -> None:
    cfg = load_scenario_experiment(repo_root() / "configs/experiments/s8_fulfillment_live.yaml")
    est = estimate_scenario(cfg)
    # 4 agent 条件（guarded を含む）× 8 seed = 32 エピソード
    assert est.agent_conditions == [
        "OR-full-llm",
        "B1-llm",
        "B0-llm",
        "OR-full-llm-guarded",
    ]
    assert est.units["episodes"] == 32
    assert est.llm_calls > 0
    assert est.embedding_calls == 0
    assert est.usd_high >= est.usd_mid >= est.usd_low > 0.0


def test_deterministic_scenario_has_zero_agent_cost() -> None:
    cfg = load_scenario_experiment(repo_root() / "configs/experiments/s8_fulfillment.yaml")
    est = estimate_scenario(cfg)
    assert est.agent_conditions == []
    assert est.llm_calls == 0
    assert est.usd_high == 0.0


def test_estimate_requires_scenario_config() -> None:
    import pytest

    with pytest.raises(TypeError):
        estimate_scenario(Path("nope"))
