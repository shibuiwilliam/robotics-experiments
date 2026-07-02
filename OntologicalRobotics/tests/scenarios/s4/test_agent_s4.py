"""S4 agent（live）射程ハーネスのオフライン検証（R-B・stub・実APIなし）。"""

from __future__ import annotations

from orx.common.providers import ProviderConfig
from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s4_inspection import runner
from orx.exp.suites.s4_inspection.agent import AGENT_CONDITIONS, parse_anchor_answer


def test_parse_anchor_answer_validates_assets_and_range() -> None:
    out = parse_anchor_answer("obs0=V-205, obs1=V-310, obs2=ghost", n_obs=2, asset_ids={"V-205"})
    assert out[0] == "V-205"
    assert out[1] is None  # 未知資産は None
    assert 2 not in out  # 範囲外 obs は無視


def test_agent_conditions_registered() -> None:
    assert set(AGENT_CONDITIONS) <= set(runner.META.conditions)


def test_agent_path_runs_offline_stub(tmp_path) -> None:
    cfg = ScenarioExperimentConfig(
        name="s4-agent-stub",
        scenario="s4",
        world_config=runner.WORLD,
        conditions=AGENT_CONDITIONS,
        seeds=[401, 402],
        duration_s=0.0,
        params={"position_noise": 0.6},
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
    assert "対応付け" in md
