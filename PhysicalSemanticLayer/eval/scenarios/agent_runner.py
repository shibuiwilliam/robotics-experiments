"""Async scenario runner with Claude Agent SDK.

Connects real Claude agents to the PSL pipeline via MCP tools.
The agent autonomously calls query_world_model, command_robot_semantic,
resolve_document_to_physical, and subscribe_affordances to execute
each scenario's task.

In online mode, the resolve_document_to_physical tool calls the
pseudo-cloud HTTP service via httpx, not direct Python imports.

All agent tests are isolated behind @pytest.mark.api and require
ANTHROPIC_API_KEY to be set.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import httpx
import structlog

from psl.world_model.core import WorldModel

logger = structlog.get_logger()


async def run_scenario_with_agents(
    scenario_id: str,
    prompt: str,
    world_model: WorldModel,
    db_conn: sqlite3.Connection,
    seed: int = 42,
    model: str = "claude-sonnet-4-6",
    temperature: float = 0.0,
    max_turns: int = 10,
    max_budget_usd: float = 0.50,
    http_client: httpx.AsyncClient | None = None,
    vla_encoder: Any | None = None,
) -> dict[str, Any]:
    """Run a scenario end-to-end with Claude Agent SDK.

    The agent receives the scenario prompt and uses MCP tools to
    interact with the world model and pseudo-cloud.

    Args:
        scenario_id: Scenario identifier.
        prompt: Task prompt for the supervisor agent.
        world_model: Shared world model (pre-populated by orchestrator).
        db_conn: SQLite connection to pseudo-cloud.
        seed: Random seed (for logging).
        model: Claude model to use.
        temperature: Agent temperature (0.0 for determinism).
        max_turns: Maximum agentic turns.
        max_budget_usd: Cost cap.
        http_client: Optional httpx client for HTTP-backed tools (online mode).
        vla_encoder: Optional VLAEncoder for embedding-based affordances.

    Returns:
        Dict with result_text, total_cost_usd, num_turns, tool_calls.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        ToolUseBlock,
        query,
    )

    from agents.topology.supervisor import build_agent_options

    opts_kwargs = build_agent_options(
        world_model,
        db_conn,
        model=model,
        temperature=temperature,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        http_client=http_client,
        vla_encoder=vla_encoder,
    )
    options = ClaudeAgentOptions(**opts_kwargs)

    import time as time_mod

    result_text = ""
    total_cost = 0.0
    num_turns = 0
    tool_calls: list[str] = []
    trace_entries: list[dict[str, object]] = []

    async for message in query(prompt=prompt, options=options):
        # Capture detailed trace (Gap 3)
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    tool_calls.append(block.name)
                    trace_entries.append(
                        {
                            "type": "tool_call",
                            "tool": block.name,
                            "input": block.input,
                            "timestamp": time_mod.time(),
                        }
                    )
                elif hasattr(block, "text"):
                    trace_entries.append(
                        {
                            "type": "reasoning",
                            "text": getattr(block, "text", ""),
                            "timestamp": time_mod.time(),
                        }
                    )

        if isinstance(message, ResultMessage):
            result_text = message.result or ""
            total_cost = message.total_cost_usd or 0.0
            num_turns = message.num_turns
            break

    logger.info(
        "agent_run_complete",
        scenario=scenario_id,
        cost_usd=total_cost,
        num_turns=num_turns,
        tool_calls_count=len(tool_calls),
        tool_calls=tool_calls,
    )

    return {
        "result_text": result_text,
        "total_cost_usd": total_cost,
        "num_turns": num_turns,
        "tool_calls": tool_calls,
        "trace": trace_entries,
        "scenario_id": scenario_id,
        "seed": seed,
    }


# ── Per-scenario agent prompts ──

_API_CONVENTIONS = (
    "You have access to a pseudo-cloud business system via MCP tools. "
    "Bin IDs use the format: 'Bin_A', 'Bin_B', 'Bin_C', 'Bin_D', 'Bin_E', 'Bin_F', 'Bin_G', 'Bin_H', "
    "'QA_TRAY', 'STAGING', 'REJECT_BIN', 'OUTPUT'. "
    "Work order IDs use format 'WO-42', 'WO-43', etc. "
    "SOP IDs use format 'SOP-PICK-001', 'SOP-HAZMAT-001', etc. "
    "Use resolve_document_to_physical with reference_type='bin' and reference_id set to the "
    "bin name (e.g. 'C' for Bin C, 'QA_TRAY' for QA tray), or reference_type='work_order' "
    "and reference_id='WO-42' for work orders. "
    "Entity IDs in the world model: 'panda_arm', 'amr_transport', 'blue_gear', 'claude_supervisor', 'pseudo_cloud'. "
)

SCENARIO_PROMPTS: dict[str, str] = {
    "s1_mixed_fleet_pick": (
        _API_CONVENTIONS
        + "Execute Work Order WO-42: retrieve the defective blue gear from bin C. "
        "Steps: 1) Use resolve_document_to_physical with reference_type='bin', reference_id='C' to find Bin C position. "
        "2) Use resolve_document_to_physical with reference_type='bin', reference_id='QA_TRAY' to find QA tray position. "
        "3) Use query_world_model with entity_id='panda_arm' to check the arm state. "
        "4) Use subscribe_affordances with entity_id='blue_gear' to check if it's graspable. "
        "5) Use command_robot_semantic to move panda_arm to the Bin C position, grasp, and place at QA tray. "
        "Report the final routing decision and all positions used."
    ),
    "s2_line_changeover": (
        _API_CONVENTIONS + "Apply manufacturing recipe R-100 to robot_a for a line changeover. "
        "1) Use resolve_document_to_physical with reference_type='work_order', reference_id='WO-42' to get context. "
        "2) Use query_world_model with entity_id='panda_arm' to check current robot state. "
        "3) Use command_robot_semantic to configure the robot with recipe targets. "
        "Report success or any safety gate rejections."
    ),
    "s3_lab_custody": (
        _API_CONVENTIONS + "Process sample SPL-001 through the lab custody protocol. "
        "1) Use resolve_document_to_physical with reference_type='work_order', reference_id='WO-42' to get work context. "
        "2) Use query_world_model with entity_id='panda_arm' to check the liquid handler state. "
        "3) Query world model for multiple entities to build the custody chain. "
        "Report the custody chain status at each processing step."
    ),
    "s4_field_inspection": (
        _API_CONVENTIONS + "Inspect equipment at bin location Bin_A. "
        "1) Use resolve_document_to_physical with reference_type='bin', reference_id='Bin_A' to find position. "
        "2) Use query_world_model with entity_id='panda_arm' to check the contact arm state. "
        "3) Use subscribe_affordances with entity_id='panda_arm' for capability check. "
        "4) Use command_robot_semantic to move to the inspection point. "
        "Report the inspection result."
    ),
    "s5_pharma_logistics": (
        _API_CONVENTIONS + "Verify and dispense pharmaceutical vial at bin C. "
        "1) Use resolve_document_to_physical with reference_type='bin', reference_id='Bin_C' to find position. "
        "2) Use query_world_model with entity_id='panda_arm' to check arm state. "
        "3) Use subscribe_affordances with entity_id='panda_arm'. "
        "Report any regulatory violations or mismatches found."
    ),
    "s6_ewaste_disassembly": (
        _API_CONVENTIONS
        + "Assess and disassemble an unknown electronic component (blue_gear entity). "
        "1) Use query_world_model with entity_id='panda_arm' to check the disassembly arm state. "
        "2) Use subscribe_affordances with entity_id='blue_gear' to determine graspability and material. "
        "3) Use command_robot_semantic to manipulate the component based on affordance assessment. "
        "Report the affordance assessment and material classification."
    ),
    "s7_degraded_ops": (
        _API_CONVENTIONS + "Complete the pick task under degraded communication conditions. "
        "Available bins: 'Bin_A', 'Bin_B', 'Bin_C', 'QA_TRAY'. "
        "1) Use resolve_document_to_physical with reference_type='bin', reference_id='Bin_C' to find the target. "
        "2) Use resolve_document_to_physical with reference_type='bin', reference_id='QA_TRAY' for destination. "
        "3) Use query_world_model with entity_id='panda_arm' — note any staleness in the data. "
        "4) Use command_robot_semantic to execute the pick despite any degradation. "
        "Report any causal ordering issues detected."
    ),
}
