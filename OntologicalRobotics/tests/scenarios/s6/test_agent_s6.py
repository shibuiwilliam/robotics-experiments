"""S6 agent（live）射程ハーネスのオフライン検証（R-3a・stub・実APIなし）。"""

from __future__ import annotations

from orx.common.providers import ProviderConfig
from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s6_recycling import runner
from orx.exp.suites.s6_recycling.agent import AGENT_CONDITIONS, parse_lane_answer


def test_parse_lane_answer_validates_lanes() -> None:
    valid = {"fire_lane", "general"}
    out = parse_lane_answer("o1=fire_lane, o2=general, o3=bogus", valid)
    assert out["o1"] == "fire_lane"
    assert out["o2"] == "general"
    assert out["o3"] == "ESCALATE"  # 未知レーンは ESCALATE へ丸める


def test_agent_conditions_registered() -> None:
    assert set(AGENT_CONDITIONS) <= set(runner.META.conditions)


def test_agent_path_runs_offline_stub(tmp_path) -> None:
    cfg = ScenarioExperimentConfig(
        name="s6-agent-stub",
        scenario="s6",
        world_config=runner.WORLD,
        conditions=AGENT_CONDITIONS,
        seeds=[601, 602],
        duration_s=0.0,
        provider=ProviderConfig(mode="stub"),
    )
    res = runner.run(cfg, tmp_path / "exp", lambda *a: None)
    assert res["scope"] == "agent"
    assert res["conditions"] == AGENT_CONDITIONS
    assert len(res["comparisons"]) == len(AGENT_CONDITIONS) - 1
    assert "agent" in runner.render_report(res)
