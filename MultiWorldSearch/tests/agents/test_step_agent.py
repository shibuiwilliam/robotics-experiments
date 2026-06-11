"""Tests for the generic scenario step-agent factory (M9)."""

import pytest

from mws.agents.scenario_agent import MockStepAgent, create_step_agent
from mws.core.config import MWSSettings
from mws.core.types import CloudMode


def _mock_fn(step: str, results: list[dict]) -> dict:
    return {"action": step, "result": f"did {step}", "status": "complete"}


def test_mock_mode_returns_mock_step_agent() -> None:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=0)
    agent = create_step_agent(
        settings, name="incident_agent", instruction="x", mock_step_fn=_mock_fn
    )
    assert isinstance(agent, MockStepAgent)
    assert agent.is_live is False
    out = agent.execute_step("confirm_leak", {"retrieval_results": []})
    assert out["status"] == "complete"


def test_live_mode_without_key_raises() -> None:
    settings = MWSSettings(cloud_mode=CloudMode.LIVE, google_api_key="")
    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        create_step_agent(settings, name="x", instruction="y", mock_step_fn=_mock_fn)


@pytest.mark.live
def test_live_step_agent_executes_real_step() -> None:
    """Live: the generic step agent makes a real ADK call (M9 acceptance)."""
    import os

    settings = MWSSettings(
        cloud_mode=CloudMode.LIVE, google_api_key=os.environ.get("GOOGLE_API_KEY", "")
    )
    agent = create_step_agent(
        settings,
        name="incident_agent",
        instruction="You are a test agent. Answer in one short sentence.",
        mock_step_fn=_mock_fn,
    )
    assert agent.is_live is True
    out = agent.execute_step(
        "confirm_leak", {"retrieval_results": [{"text": "substance_X leak in room_A"}]}
    )
    assert out["status"] == "complete"
    assert len(out["result"]) > 0
