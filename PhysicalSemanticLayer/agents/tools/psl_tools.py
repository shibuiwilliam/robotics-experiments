"""PSL MCP tools — in-process tools for Claude Agent SDK.

These are the A2R standard interface:
  - query_world_model: Read entity state from the world model
  - command_robot_semantic: Send a semantic command to a robot
  - subscribe_affordances: Get available affordances for an entity
  - resolve_document_to_physical: Resolve a document reference to a physical location

These tools are registered as in-process MCP tools via create_sdk_mcp_server.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pseudo_cloud.data import resolve_bin_to_position
from psl.world_model.core import WorldModel


def make_query_world_model_handler(
    world_model: WorldModel,
) -> Any:
    """Create handler for query_world_model tool.

    Args:
        world_model: The shared world model instance.

    Returns:
        Async handler function.
    """

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        entity_id = args["entity_id"]
        try:
            phytes = world_model.read(entity_id)
            state: dict[str, Any] = {}
            for name, phyte in phytes.items():
                state[name] = {
                    "semantic_id": phyte.semantic_id,
                    "value": phyte.value.tolist(),
                    "unit": phyte.unit,
                    "frame": phyte.frame,
                    "timestamp": phyte.timestamp,
                    "confidence": phyte.provenance.confidence,
                }
            return {"content": [{"type": "text", "text": json.dumps(state, indent=2)}]}
        except KeyError:
            return {
                "content": [{"type": "text", "text": f"Entity '{entity_id}' not found"}],
                "is_error": True,
            }

    return handler


def make_command_robot_semantic_handler(
    world_model: WorldModel,
) -> Any:
    """Create handler for command_robot_semantic tool.

    A semantic command like "move to position [x, y, z]" or "grasp object".
    In Phase 3, this writes target state to the world model.

    Args:
        world_model: The shared world model instance.

    Returns:
        Async handler function.
    """

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        entity_id = args["entity_id"]
        command = args["command"]
        params = args.get("params", {})

        # For now, log the command and return acknowledgment
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "status": "acknowledged",
                            "entity_id": entity_id,
                            "command": command,
                            "params": params,
                        }
                    ),
                }
            ]
        }

    return handler


def make_resolve_document_handler(
    db_conn: sqlite3.Connection,
) -> Any:
    """Create handler for resolve_document_to_physical tool.

    Resolves document references (bin names, work order IDs) to
    physical positions in the world frame.

    Args:
        db_conn: SQLite connection to pseudo-cloud data.

    Returns:
        Async handler function.
    """

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        ref_type = args["reference_type"]
        ref_id = args["reference_id"]

        if ref_type == "bin":
            pos = resolve_bin_to_position(db_conn, ref_id)
            if pos is not None:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "resolved": True,
                                    "reference_type": "bin",
                                    "reference_id": ref_id,
                                    "frame": "world",
                                    "position": pos,
                                }
                            ),
                        }
                    ]
                }
            return {
                "content": [{"type": "text", "text": f"Bin '{ref_id}' not found"}],
                "is_error": True,
            }

        elif ref_type == "work_order":
            from pseudo_cloud.data import query_work_order

            wo = query_work_order(db_conn, ref_id)
            if wo is not None:
                # Resolve bins mentioned in the work order
                source_pos = resolve_bin_to_position(db_conn, str(wo.get("source_bin", "")))
                target_pos = resolve_bin_to_position(db_conn, str(wo.get("target_location", "")))
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "resolved": True,
                                    "work_order": dict(wo),
                                    "source_position": source_pos,
                                    "target_position": target_pos,
                                }
                            ),
                        }
                    ]
                }
            return {
                "content": [{"type": "text", "text": f"Work order '{ref_id}' not found"}],
                "is_error": True,
            }

        return {
            "content": [{"type": "text", "text": f"Unknown reference type: {ref_type}"}],
            "is_error": True,
        }

    return handler


def make_subscribe_affordances_handler(
    world_model: WorldModel,
    vla_encoder: Any | None = None,
) -> Any:
    """Create handler for subscribe_affordances tool.

    Returns affordances for an entity based on its current state
    and the world model context. When a VLA encoder is provided,
    includes embedding-based affordance predictions.

    Args:
        world_model: The shared world model instance.
        vla_encoder: Optional VLAEncoder for embedding-based affordances.

    Returns:
        Async handler function.
    """

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        entity_id = args["entity_id"]
        try:
            entity = world_model.get_entity(entity_id)
            # Rule-based affordance computation
            affordances: list[str] = []
            if entity.phytes:
                affordances.append("observable")
            if any(k.startswith("joint_") for k in entity.phytes):
                affordances.extend(["movable", "controllable"])
            if entity.entity_id.startswith("blue_gear") or entity.entity_id.startswith("gear"):
                affordances.extend(["graspable", "transportable"])

            # VLA-based affordance enrichment (when encoder is available)
            vla_prediction: dict[str, object] | None = None
            if vla_encoder is not None:
                pred = vla_encoder.predict_affordances(entity_id)
                vla_prediction = {
                    "graspable": pred.graspable,
                    "detachable": pred.detachable,
                    "material": pred.material,
                    "confidence": pred.confidence,
                }
                if pred.graspable and "graspable" not in affordances:
                    affordances.append("graspable")

            result: dict[str, object] = {"entity_id": entity_id, "affordances": affordances}
            if vla_prediction is not None:
                result["vla_prediction"] = vla_prediction

            return {"content": [{"type": "text", "text": json.dumps(result)}]}
        except KeyError:
            return {
                "content": [{"type": "text", "text": f"Entity '{entity_id}' not found"}],
                "is_error": True,
            }

    return handler
