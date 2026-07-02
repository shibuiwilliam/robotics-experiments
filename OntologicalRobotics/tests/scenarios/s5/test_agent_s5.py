"""S5 agent（live）射程ハーネスのオフライン検証（R-B・stub・実APIなし）。"""

from __future__ import annotations

from orx.common.providers import ProviderConfig
from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s5_hospital import runner
from orx.exp.suites.s5_hospital.agent import (
    AGENT_CONDITIONS,
    adjacency,
    is_valid_path,
    parse_route_answer,
)


def test_parse_route_answer_splits_and_filters() -> None:
    out = parse_route_answer("t1=a>b>c, t2=x>y, t9=p>q", {"t1", "t2"})
    assert out["t1"] == ["a", "b", "c"]
    assert out["t2"] == ["x", "y"]
    assert "t9" not in out  # 未知 transport_id は無視


def test_is_valid_path_checks_adjacency_and_endpoints() -> None:
    adj = adjacency([["a", "b"], ["b", "c"]])
    assert is_valid_path(["a", "b", "c"], "a", "c", adj) is True
    assert is_valid_path(["a", "c"], "a", "c", adj) is False  # a-c は非隣接
    assert is_valid_path(["a", "b"], "a", "c", adj) is False  # dst 不一致


def test_agent_conditions_registered() -> None:
    assert set(AGENT_CONDITIONS) <= set(runner.META.conditions)


def test_agent_path_runs_offline_stub(tmp_path) -> None:
    cfg = ScenarioExperimentConfig(
        name="s5-agent-stub",
        scenario="s5",
        world_config=runner.WORLD,
        conditions=AGENT_CONDITIONS,
        seeds=[501, 502],
        duration_s=0.0,
        provider=ProviderConfig(mode="stub"),
    )
    res = runner.run(cfg, tmp_path / "exp", lambda *a: None)
    assert res["scope"] == "agent"
    assert res["conditions"] == AGENT_CONDITIONS
    assert "mode=stub" in res["model_snapshot"]
    assert len(res["comparisons"]) == len(AGENT_CONDITIONS) - 1
    assert set(res["total_tokens"]) == set(AGENT_CONDITIONS)
    md = runner.render_report(res)
    assert "agent/live" in md
    assert "規範ルーティング" in md
