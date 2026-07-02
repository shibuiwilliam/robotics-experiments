"""S1 agent（live）射程ハーネスのオフライン検証（R-3a）。

stub プロバイダで agent 経路が**実APIを叩かずに**通ること、解答パースが正しいことを検証。
数値（正答率）は stub では無意味なので、構造（scope/conditions/comparisons/tokens）のみ検査。
"""

from __future__ import annotations

from orx.common.providers import ProviderConfig
from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s1_lot_recall import runner
from orx.exp.suites.s1_lot_recall.agent import AGENT_CONDITIONS, parse_recall_answer


def test_parse_recall_answer_maps_bc_zone() -> None:
    a = parse_recall_answer("BC-101=shelf_a, BC-103=handoff, BC-106=unknown")
    assert a.targets == {"BC-101": "shelf_a", "BC-103": "handoff", "BC-106": None}


def test_parse_recall_answer_none() -> None:
    assert parse_recall_answer("none").targets == {}
    assert parse_recall_answer("").targets == {}


def test_agent_conditions_registered() -> None:
    assert set(AGENT_CONDITIONS) <= set(runner.META.conditions)


def test_agent_path_runs_offline_stub(tmp_path) -> None:
    cfg = ScenarioExperimentConfig(
        name="s1-agent-stub",
        scenario="s1",
        world_config=runner.WORLD,
        conditions=AGENT_CONDITIONS,
        seeds=[101, 102],
        duration_s=18.0,
        claim_ttl_s=8.0,
        provider=ProviderConfig(mode="stub"),
    )
    res = runner.run(cfg, tmp_path / "exp", lambda *a: None)
    assert res["scope"] == "agent"
    assert res["conditions"] == AGENT_CONDITIONS
    assert "mode=stub" in res["model_snapshot"]
    assert len(res["comparisons"]) == len(AGENT_CONDITIONS) - 1
    assert set(res["total_tokens"]) == set(AGENT_CONDITIONS)
    # agent 射程レポートが生成でき、scope セクションを含む
    md = runner.render_report(res)
    assert "agent射程" in md
    assert "トークン効率" in md
