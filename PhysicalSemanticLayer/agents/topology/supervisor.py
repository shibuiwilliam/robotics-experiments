"""Agent topology — supervisor + worker configuration.

The supervisor plans and delegates. Workers execute via MCP tools.
Workers NEVER access ground truth or world model internals directly.

This module provides the configuration factory for the Claude Agent SDK.
Supports both offline (direct Python handlers) and online (HTTP-backed) modes.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import httpx

from psl.world_model.core import WorldModel


def build_agent_options(
    world_model: WorldModel,
    db_conn: sqlite3.Connection,
    model: str = "claude-sonnet-4-6",
    temperature: float = 0.0,
    max_turns: int = 10,
    max_budget_usd: float = 0.50,
    http_client: httpx.AsyncClient | None = None,
    vla_encoder: Any | None = None,
) -> dict[str, Any]:
    """Build ClaudeAgentOptions kwargs for the PSL supervisor agent.

    Creates the in-process MCP server with PSL tools and configures
    the supervisor + worker topology.

    Args:
        world_model: Shared world model instance.
        db_conn: SQLite connection for pseudo-cloud data.
        model: Claude model to use.
        temperature: Agent temperature (0.0 for determinism).
        max_turns: Maximum agentic turns.
        max_budget_usd: Cost cap.
        http_client: Optional httpx client for HTTP-backed handlers (online mode).
        vla_encoder: Optional VLAEncoder for embedding-based affordances.

    Returns:
        Dict of kwargs suitable for ClaudeAgentOptions constructor.
    """
    from claude_agent_sdk import AgentDefinition, create_sdk_mcp_server, tool

    from agents.tools.psl_tools import (
        make_command_robot_semantic_handler,
        make_query_world_model_handler,
        make_resolve_document_handler,
        make_subscribe_affordances_handler,
    )

    # Define MCP tools
    @tool(
        "query_world_model",
        "Query the current state of an entity in the shared world model. Returns Phyte data.",
        {"entity_id": str},
    )
    async def query_wm(args: dict[str, Any]) -> dict[str, Any]:
        handler = make_query_world_model_handler(world_model)
        return await handler(args)

    @tool(
        "command_robot_semantic",
        "Send a semantic command to a robot (e.g., 'move_to', 'grasp'). "
        "Params should include target positions, object IDs, etc.",
        {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Robot entity ID"},
                "command": {"type": "string", "description": "Semantic command name"},
                "params": {"type": "object", "description": "Command parameters"},
            },
            "required": ["entity_id", "command"],
        },
    )
    async def command_robot(args: dict[str, Any]) -> dict[str, Any]:
        handler = make_command_robot_semantic_handler(world_model)
        return await handler(args)

    @tool(
        "resolve_document_to_physical",
        "Resolve a document reference (bin name, work order ID) to a physical "
        "location in the world frame. Use this to ground symbolic references.",
        {
            "type": "object",
            "properties": {
                "reference_type": {
                    "type": "string",
                    "enum": ["bin", "work_order"],
                    "description": "Type of document reference",
                },
                "reference_id": {"type": "string", "description": "The reference ID to resolve"},
            },
            "required": ["reference_type", "reference_id"],
        },
    )
    async def resolve_doc(args: dict[str, Any]) -> dict[str, Any]:
        # Use HTTP-backed handler in online mode, direct Python in offline
        if http_client is not None:
            from agents.tools.psl_tools_http import make_resolve_document_handler_http

            handler = make_resolve_document_handler_http(http_client)
        else:
            handler = make_resolve_document_handler(db_conn)
        return await handler(args)

    @tool(
        "subscribe_affordances",
        "Get available affordances for an entity (graspable, movable, etc). "
        "Includes VLA embedding-based predictions when available.",
        {"entity_id": str},
    )
    async def get_affordances(args: dict[str, Any]) -> dict[str, Any]:
        handler = make_subscribe_affordances_handler(world_model, vla_encoder=vla_encoder)
        return await handler(args)

    # Create in-process MCP server
    psl_server = create_sdk_mcp_server(
        name="psl",
        version="0.1.0",
        tools=[query_wm, command_robot, resolve_doc, get_affordances],
    )

    # Worker agent definition
    worker_def = AgentDefinition(
        description="PSL worker agent that executes tasks using PSL tools. "
        "Can query the world model, command robots, resolve document references, "
        "and check affordances.",
        prompt=(
            "You are a worker agent for PSL-Bench. You execute specific tasks "
            "given by the supervisor. Use the PSL tools to interact with the world model "
            "and robots. Always report results back clearly."
        ),
        tools=[
            "mcp__psl__query_world_model",
            "mcp__psl__command_robot_semantic",
            "mcp__psl__resolve_document_to_physical",
            "mcp__psl__subscribe_affordances",
        ],
    )

    return {
        "model": model,
        "mcp_servers": {"psl": psl_server},
        "allowed_tools": [
            "mcp__psl__query_world_model",
            "mcp__psl__command_robot_semantic",
            "mcp__psl__resolve_document_to_physical",
            "mcp__psl__subscribe_affordances",
            "Agent",
        ],
        "agents": {"worker": worker_def},
        "max_turns": max_turns,
        "max_budget_usd": max_budget_usd,
    }
