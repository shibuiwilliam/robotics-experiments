"""Tests for mock agent."""

from mws.agents.mock import MockOpsAgent


def test_plan() -> None:
    agent = MockOpsAgent(seed=0)
    plan = agent.plan({})
    assert len(plan) > 0
    assert "assess_situation" in plan


def test_execute_step() -> None:
    agent = MockOpsAgent(seed=0)
    result = agent.execute_step("assess_situation", {"retrieval_results": []})
    assert result["status"] == "complete"


def test_full_plan_execution() -> None:
    agent = MockOpsAgent(seed=0)
    plan = agent.plan({})
    for step in plan:
        result = agent.execute_step(step, {"retrieval_results": []})
        assert result["status"] == "complete"
