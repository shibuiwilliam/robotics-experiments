"""Pseudo-cloud HTTP service — Starlette server wrapping SQLite business data.

Phase C: External service boundary for the pseudo-cloud. Agents interact
with business data through HTTP APIs, just as they would with real
WMS/ERP/LIMS systems in production.

Usage:
    python -m pseudo_cloud.server          # start on port 8042
    make pseudo-cloud-start                # via Makefile
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import structlog
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from pseudo_cloud.data import (
    init_db,
    query_inventory,
    query_sop,
    query_work_order,
    resolve_bin_to_position,
)

logger = structlog.get_logger("pseudo_cloud.server")

# Module-level DB connection (lazy init)
_db_conn: Any = None


def _get_db() -> Any:
    """Get or create the DB connection (lazy singleton)."""
    global _db_conn
    if _db_conn is None:
        _db_conn = init_db()
    return _db_conn


def _reset_db() -> None:
    """Reset the DB connection (for test isolation)."""
    global _db_conn
    _db_conn = None


# ---------------------------------------------------------------------------
# Request stats
# ---------------------------------------------------------------------------

_request_stats: dict[str, int] = {"total": 0, "404": 0}


def get_request_stats() -> dict[str, int]:
    return dict(_request_stats)


def reset_request_stats() -> None:
    _request_stats["total"] = 0
    _request_stats["404"] = 0


# ---------------------------------------------------------------------------
# Middleware: fault injection + request logging
# ---------------------------------------------------------------------------


class FaultInjectionMiddleware(BaseHTTPMiddleware):
    """Inject faults via request headers for testing resilience.

    Headers:
        X-Inject-Error: "500" — return HTTP 500 before processing.
        X-Inject-Delay-Ms: "<int>" — sleep for the given milliseconds.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()

        # Fault: forced error
        error_code = request.headers.get("X-Inject-Error")
        if error_code == "500":
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.info(
                "request",
                method=request.method,
                path=str(request.url.path),
                status_code=500,
                latency_ms=round(elapsed_ms, 2),
                fault="injected_500",
            )
            return JSONResponse({"error": "injected server error"}, status_code=500)

        # Fault: injected delay
        delay_header = request.headers.get("X-Inject-Delay-Ms")
        if delay_header:
            delay_s = int(delay_header) / 1000.0
            await asyncio.sleep(delay_s)

        response = await call_next(request)

        _request_stats["total"] += 1
        if response.status_code == 404:
            _request_stats["404"] += 1

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "request",
            method=request.method,
            path=str(request.url.path),
            status_code=response.status_code,
            latency_ms=round(elapsed_ms, 2),
        )
        return response


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def get_workorder(request: Request) -> JSONResponse:
    wo_id = request.path_params["wo_id"]
    result = query_work_order(_get_db(), wo_id)
    if result is None:
        return JSONResponse({"error": f"Work order '{wo_id}' not found"}, status_code=404)
    return JSONResponse({"data": result})


async def get_inventory_item(request: Request) -> JSONResponse:
    item_id = request.path_params["item_id"]
    result = query_inventory(_get_db(), item_id)
    if result is None:
        return JSONResponse({"error": f"Item '{item_id}' not found"}, status_code=404)
    return JSONResponse({"data": result})


async def list_inventory(request: Request) -> JSONResponse:
    """List all inventory items with optional pagination.

    Query params:
        limit  — max items to return (default 50).
        offset — number of items to skip (default 0).
    """
    limit = int(request.query_params.get("limit", "50"))
    offset = int(request.query_params.get("offset", "0"))

    db = _get_db()
    total_row = db.execute("SELECT COUNT(*) FROM inventory").fetchone()
    total: int = total_row[0]

    rows = db.execute(
        "SELECT * FROM inventory ORDER BY item_id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()

    items = [dict(r) for r in rows]
    return JSONResponse({"items": items, "total": total, "limit": limit, "offset": offset})


async def create_work_order(request: Request) -> JSONResponse:
    """Create a new work order.

    Required JSON body fields: item_id, description, source_bin, target_bin.
    Validates that item_id exists in inventory.
    """
    body = await request.json()

    # Validate required fields
    required = ("item_id", "description", "source_bin", "target_bin")
    missing = [f for f in required if f not in body]
    if missing:
        return JSONResponse(
            {"error": f"Missing required fields: {', '.join(missing)}"}, status_code=400
        )

    # Validate item exists in inventory
    item = query_inventory(_get_db(), body["item_id"])
    if item is None:
        return JSONResponse(
            {"error": f"Item '{body['item_id']}' not found in inventory"}, status_code=400
        )

    # Generate a new work order ID
    db = _get_db()
    row = db.execute(
        "SELECT work_order_id FROM work_orders ORDER BY work_order_id DESC LIMIT 1"
    ).fetchone()
    if row:
        last_id = row["work_order_id"]  # type: ignore[index]
        # Extract numeric part after "WO-"
        num = int(str(last_id).split("-")[1]) + 1
    else:
        num = 100
    new_id = f"WO-{num}"

    db.execute(
        "INSERT INTO work_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            new_id,
            body.get("title", body["description"][:60]),
            body["description"],
            body["source_bin"],
            body["target_bin"],
            body["item_id"],
            "pending",
            body.get("priority", "normal"),
        ),
    )
    db.commit()

    return JSONResponse({"data": {"work_order_id": new_id, "status": "pending"}}, status_code=201)


async def get_bin(request: Request) -> JSONResponse:
    bin_id = request.path_params["bin_id"]
    position = resolve_bin_to_position(_get_db(), bin_id)
    if position is None:
        return JSONResponse({"error": f"Bin '{bin_id}' not found"}, status_code=404)
    return JSONResponse({"data": {"bin_id": bin_id, "position": position, "frame": "world"}})


async def get_sop(request: Request) -> JSONResponse:
    sop_id = request.path_params["sop_id"]
    result = query_sop(_get_db(), sop_id)
    if result is None:
        return JSONResponse({"error": f"SOP '{sop_id}' not found"}, status_code=404)
    return JSONResponse({"data": result})


async def create_exception(request: Request) -> JSONResponse:
    body = await request.json()
    return JSONResponse({"status": "created", "exception": body}, status_code=201)


async def list_bins(request: Request) -> JSONResponse:
    """List all bin IDs and positions."""
    import json as _json

    db = _get_db()
    rows = db.execute("SELECT bin_id, position_json FROM bin_locations ORDER BY bin_id").fetchall()
    bins = [
        {
            "bin_id": r["bin_id"],
            "position": _json.loads(r["position_json"]),
            "frame": "world",
        }
        for r in rows
    ]
    return JSONResponse({"bins": bins})


async def list_workorders(request: Request) -> JSONResponse:
    """List all work orders with optional pagination."""
    limit = int(request.query_params.get("limit", "50"))
    offset = int(request.query_params.get("offset", "0"))
    db = _get_db()
    total_row = db.execute("SELECT COUNT(*) FROM work_orders").fetchone()
    total: int = total_row[0]
    rows = db.execute(
        "SELECT * FROM work_orders ORDER BY work_order_id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    items = [dict(r) for r in rows]
    return JSONResponse({"items": items, "total": total, "limit": limit, "offset": offset})


async def list_sops(request: Request) -> JSONResponse:
    """List all SOP IDs and titles."""
    db = _get_db()
    rows = db.execute("SELECT sop_id, title FROM sops ORDER BY sop_id").fetchall()
    sops = [{"sop_id": r["sop_id"], "title": r["title"]} for r in rows]
    return JSONResponse({"sops": sops})


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy", "service": "pseudo-cloud"})


# ---------------------------------------------------------------------------
# App assembly
# ---------------------------------------------------------------------------

routes = [
    Route("/health", health),
    Route("/api/v1/workorders", list_workorders),  # list BEFORE {wo_id}
    Route("/api/v1/workorders", create_work_order, methods=["POST"]),
    Route("/api/v1/workorders/{wo_id}", get_workorder),
    Route("/api/v1/inventory", list_inventory),  # list BEFORE {item_id}
    Route("/api/v1/inventory/{item_id}", get_inventory_item),
    Route("/api/v1/bins", list_bins),  # list BEFORE {bin_id}
    Route("/api/v1/bins/{bin_id}", get_bin),
    Route("/api/v1/sops", list_sops),  # list BEFORE {sop_id}
    Route("/api/v1/sops/{sop_id}", get_sop),
    Route("/api/v1/exceptions", create_exception, methods=["POST"]),
]

middleware = [
    Middleware(FaultInjectionMiddleware),
]

app = Starlette(routes=routes, middleware=middleware)


def main() -> None:
    """Run the pseudo-cloud server."""
    import uvicorn

    port = int(os.environ.get("PSEUDO_CLOUD_PORT", "8042"))
    print(f"Starting pseudo-cloud server on port {port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
