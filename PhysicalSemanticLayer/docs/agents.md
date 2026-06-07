# Agent Architecture & MCP Tool Specification

## Topology

Supervisor + Worker pattern via Claude Agent SDK subagents:

- **Supervisor**: Plans task execution, delegates to workers. Uses the `Agent` built-in tool to spawn workers.
- **Worker**: Executes specific tasks using PSL MCP tools. Cannot access ground truth or world model internals directly.

## MCP Tools (in-process via `create_sdk_mcp_server`)

All tools are registered on a single in-process MCP server named `psl`.
Tool names follow the pattern `mcp__psl__<tool_name>`.

### query_world_model

Read the current Phyte state of an entity from the shared world model.

| Field | Type | Description |
|-------|------|-------------|
| `entity_id` | `str` | Entity to query |

**Returns**: JSON with named Phyte summaries (semantic_id, value, unit, frame, timestamp, confidence).

### command_robot_semantic

Send a semantic command to a robot (e.g., "move_to", "grasp").

| Field | Type | Description |
|-------|------|-------------|
| `entity_id` | `str` | Target robot |
| `command` | `str` | Semantic command name |
| `params` | `object` | Command-specific parameters |

**Returns**: Acknowledgment with status.

### resolve_document_to_physical

Resolve a document reference to a physical location in the world frame.
This is the agent-facing API for mechanism 10 (document anchoring).

| Field | Type | Description |
|-------|------|-------------|
| `reference_type` | `"bin"` or `"work_order"` | Type of reference |
| `reference_id` | `str` | The reference to resolve |

**Returns**: Physical position in world frame, or error if not found.

### subscribe_affordances

Get available affordances for an entity based on world model state.

| Field | Type | Description |
|-------|------|-------------|
| `entity_id` | `str` | Entity to inspect |

**Returns**: List of affordance strings (e.g., "graspable", "movable").

## Configuration

Agent settings are pinned in experiment YAML configs:

```yaml
agent:
  model: "claude-sonnet-4-6"
  temperature: 0.0
  max_turns: 10
  max_budget_usd: 0.50
```

## Cost Discipline

- All API calls log `usage` and `total_cost_usd`
- Tests marked `@pytest.mark.api` are excluded from `make check`
- Cost estimates are required before any sweep involving agents

## Implementation

- Tool handlers: `agents/tools/psl_tools.py`
- Topology config: `agents/topology/supervisor.py`
- Smoke test: `agents/smoke.py` (requires API key)
