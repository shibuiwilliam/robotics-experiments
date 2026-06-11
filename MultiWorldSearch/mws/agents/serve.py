"""Agent serve command — starts an agent with MWS search tools."""

from __future__ import annotations

from mws.core.config import get_settings
from mws.core.logging import get_logger

logger = get_logger(__name__)


def serve_agent(config_path: str, cloud_mode: str) -> None:
    """Start an agent server (mock mode: just logs readiness)."""
    settings = get_settings()
    logger.info(
        "Agent server starting",
        config=config_path,
        cloud_mode=settings.cloud_mode,
    )

    from mws.agents.ops_agent import create_ops_agent

    agent = create_ops_agent(settings)
    logger.info("Agent ready", agent_type=type(agent).__name__)

    # In a real implementation, this would start an HTTP/gRPC server
    # with MWS search tools exposed as MCP-compatible endpoints.
    logger.info("Mock agent serve complete (no persistent server in mock mode)")
