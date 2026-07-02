"""S2 agent（live）射程ハーネスのオフライン検証（R-3a・stub・実APIなし）。"""

from __future__ import annotations

from orx.common.providers import ProviderConfig
from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s2_allergen import runner
from orx.exp.suites.s2_allergen.agent import AGENT_CONDITIONS, parse_yes_no


def test_parse_yes_no() -> None:
    assert parse_yes_no("ANSWER: yes") is True
    assert parse_yes_no("no") is False
    assert parse_yes_no("はい") is True
    assert parse_yes_no("いいえ") is False
    assert parse_yes_no("???", default=True) is True


def test_agent_conditions_registered() -> None:
    assert set(AGENT_CONDITIONS) <= set(runner.META.conditions)


def test_agent_path_runs_offline_stub(tmp_path) -> None:
    cfg = ScenarioExperimentConfig(
        name="s2-agent-stub",
        scenario="s2",
        world_config=runner.WORLD,
        conditions=AGENT_CONDITIONS,
        seeds=[201, 202],
        duration_s=0.0,
        provider=ProviderConfig(mode="stub"),
    )
    res = runner.run(cfg, tmp_path / "exp", lambda *a: None)
    assert res["scope"] == "agent"
    assert res["conditions"] == AGENT_CONDITIONS
    assert len(res["comparisons"]) == len(AGENT_CONDITIONS) - 1
    assert set(res["total_tokens"]) == set(AGENT_CONDITIONS)
    assert "agent" in runner.render_report(res)
