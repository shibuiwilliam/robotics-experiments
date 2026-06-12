"""Generic per-scenario step agent factory (IMPROVEMENT M9).

Until now only S1 had a live ADK path; other scenarios executed mock step
dictionaries even in live mode, which made "LLM plan" claims live-verifiable
for S1 only. This factory gives every scenario the same pattern:

- mock mode → :class:`MockStepAgent` wrapping the scenario's deterministic
  step function (no cloud, no keys, identical to the previous behavior).
- live mode → the generalized ADK agent (gemini-3.5-flash) with a
  scenario-specific name/instruction. Callers must record these calls with
  ``record_llm_call(real=True)`` and wrap them in the ``gemini_infer``
  latency bucket.

Step COMPLETION remains gated on retrieved evidence in the scenarios — the
agent (mock or live) supplies the narrative, never the verdict.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mws.core.config import MWSSettings
from mws.core.logging import get_logger
from mws.core.types import CloudMode

logger = get_logger(__name__)

MockStepFn = Callable[[str, list[dict]], dict[str, Any]]


class MockStepAgent:
    """Deterministic local step agent wrapping a scenario's mock step function."""

    is_live = False

    def __init__(self, step_fn: MockStepFn) -> None:
        self._step_fn = step_fn

    def execute_step(self, step: str, context: dict[str, Any]) -> dict[str, Any]:
        return self._step_fn(step, context.get("retrieval_results", []))


def create_step_agent(
    settings: MWSSettings,
    *,
    name: str,
    instruction: str,
    mock_step_fn: MockStepFn,
    call_recorder: Any = None,
) -> Any:
    """Create a scenario step agent: live ADK when CloudMode.LIVE, else mock.

    ``call_recorder`` (LLMCallRecorder, IMPROVEMENT M16) is wired into the
    live agent only — mock agents make no real calls and record nothing.

    Raises:
        ValueError: live mode without GOOGLE_API_KEY.
    """
    if settings.cloud_mode == CloudMode.LIVE:
        if not settings.google_api_key:
            raise ValueError(
                "MWS_CLOUD_MODE=live requires GOOGLE_API_KEY for the LLM agent. "
                "Set the env var or switch to MWS_CLOUD_MODE=mock."
            )
        from mws.agents.live import ADKOpsAgent
        from mws.agents.ops_agent import _replay_source

        replay_source = _replay_source(settings)
        logger.info("Creating live ADK step agent", name=name, replay=replay_source is not None)
        agent = ADKOpsAgent(
            api_key=settings.google_api_key,
            seed=settings.seed,
            name=name,
            instruction=instruction,
            # Replayed runs must not re-record (audit third leg stays honest).
            call_recorder=None if replay_source is not None else call_recorder,
            replay_source=replay_source,
        )
        agent.is_live = True  # type: ignore[attr-defined]
        return agent

    return MockStepAgent(mock_step_fn)
