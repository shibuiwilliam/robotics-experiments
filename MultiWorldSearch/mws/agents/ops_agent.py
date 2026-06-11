"""Operations agent factory — returns mock or live agent based on cloud mode.

Mock mode: MockOpsAgent (deterministic, no cloud calls).
Live mode: ADKOpsAgent (Gemini ADK with gemini-3.5-flash, MWS search as tool).
"""

from __future__ import annotations

from typing import Any

from mws.agents.mock import MockOpsAgent
from mws.core.config import MWSSettings
from mws.core.logging import get_logger
from mws.core.types import CloudMode

logger = get_logger(__name__)


def create_ops_agent(
    settings: MWSSettings,
    search_fn: Any = None,
    call_recorder: Any = None,
) -> Any:
    """Create an operations agent based on cloud mode.

    In mock mode: returns MockOpsAgent (deterministic).
    In live mode: returns ADKOpsAgent (Gemini ADK with tool-use).

    Args:
        settings: MWS settings.
        search_fn: Optional MWS search function to register as an ADK tool.
        call_recorder: Optional LLMCallRecorder — real calls append to the
            run's llm_calls.jsonl (IMPROVEMENT M16). Ignored in mock mode.

    Raises:
        ValueError: If live mode but GOOGLE_API_KEY is missing.
    """
    if settings.cloud_mode == CloudMode.LIVE:
        if not settings.google_api_key:
            raise ValueError(
                "MWS_CLOUD_MODE=live requires GOOGLE_API_KEY for the LLM agent. "
                "Set the env var or switch to MWS_CLOUD_MODE=mock."
            )
        from mws.agents.live import ADKOpsAgent

        logger.info("Creating live ADK ops agent")
        return ADKOpsAgent(
            api_key=settings.google_api_key,
            seed=settings.seed,
            search_fn=search_fn,
            call_recorder=call_recorder,
        )

    return MockOpsAgent(seed=settings.seed)
