"""S3 agent（live）射程ハーネスのオフライン検証（R-B・stub・実APIなし）。"""

from __future__ import annotations

from orx.common.providers import ProviderConfig
from orx.exp.scenario import ScenarioExperimentConfig
from orx.exp.suites.s3_multi_vendor import runner
from orx.exp.suites.s3_multi_vendor.agent import AGENT_CONDITIONS, parse_alloc_answer


def test_parse_alloc_answer_validates_machines() -> None:
    out = parse_alloc_answer("task0=m_a1, task1=m_b2, task2=ghost", {"m_a1", "m_b2"})
    assert out["task0"] == "m_a1"
    assert out["task1"] == "m_b2"
    assert out["task2"] is None  # 未知機体は None（不適格扱い）


def test_agent_conditions_registered() -> None:
    assert set(AGENT_CONDITIONS) <= set(runner.META.conditions)


def test_agent_path_runs_offline_stub(tmp_path) -> None:
    cfg = ScenarioExperimentConfig(
        name="s3-agent-stub",
        scenario="s3",
        world_config=runner.WORLD,
        conditions=AGENT_CONDITIONS,
        seeds=[301, 302],
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
    assert "割当正答" in md
