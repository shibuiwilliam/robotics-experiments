"""HTTP-backed MCP tool handlers for online mode.

These handlers call the pseudo-cloud HTTP service via httpx instead of
importing pseudo_cloud.data directly. This tests the real serialization
boundary (JSON over HTTP) that production systems would have.

Used when ExecutionMode.ONLINE: the orchestrator creates an httpx.AsyncClient
with ASGITransport pointing at the Starlette app, and passes it here.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


def make_resolve_document_handler_http(
    http_client: httpx.AsyncClient,
) -> Any:
    """Create HTTP-backed handler for resolve_document_to_physical.

    Calls the pseudo-cloud REST API instead of importing Python modules.

    Args:
        http_client: httpx.AsyncClient (may use ASGITransport for in-process).

    Returns:
        Async handler function.
    """

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        ref_type = args["reference_type"]
        ref_id = args["reference_id"]

        if ref_type == "bin":
            response = await http_client.get(f"/api/v1/bins/{ref_id}")
            if response.status_code == 200:
                data = response.json()["data"]
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "resolved": True,
                                    "reference_type": "bin",
                                    "reference_id": ref_id,
                                    "frame": data.get("frame", "world"),
                                    "position": data["position"],
                                }
                            ),
                        }
                    ]
                }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Bin '{ref_id}' not found (HTTP {response.status_code})",
                    }
                ],
                "is_error": True,
            }

        elif ref_type == "work_order":
            response = await http_client.get(f"/api/v1/workorders/{ref_id}")
            if response.status_code == 200:
                wo_data = response.json()["data"]
                # Resolve source and target bin positions
                source_pos = None
                target_pos = None
                source_bin = wo_data.get("source_bin", "")
                target_loc = wo_data.get("target_location", "")

                if source_bin:
                    bin_resp = await http_client.get(f"/api/v1/bins/{source_bin}")
                    if bin_resp.status_code == 200:
                        source_pos = bin_resp.json()["data"]["position"]

                if target_loc:
                    bin_resp = await http_client.get(f"/api/v1/bins/{target_loc}")
                    if bin_resp.status_code == 200:
                        target_pos = bin_resp.json()["data"]["position"]

                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "resolved": True,
                                    "work_order": wo_data,
                                    "source_position": source_pos,
                                    "target_position": target_pos,
                                }
                            ),
                        }
                    ]
                }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Work order '{ref_id}' not found (HTTP {response.status_code})",
                    }
                ],
                "is_error": True,
            }

        return {
            "content": [{"type": "text", "text": f"Unknown reference type: {ref_type}"}],
            "is_error": True,
        }

    return handler
