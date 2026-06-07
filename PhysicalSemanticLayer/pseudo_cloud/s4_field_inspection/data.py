"""S4 pseudo-cloud: Asset registry, maintenance manuals, work order generation."""

from __future__ import annotations

import json
import sqlite3

ASSETS = [
    {
        "asset_id": "AST-001",
        "name": "Pump-Flange-A",
        "position": [1.0, 0.5, 0.3],
        "frame": "world",
        "condition": "corroded",
    },
    {
        "asset_id": "AST-002",
        "name": "Valve-Body-B",
        "position": [2.0, -0.3, 0.5],
        "frame": "world",
        "condition": "ok",
    },
    {
        "asset_id": "AST-003",
        "name": "Pipe-Joint-C",
        "position": [0.5, 1.0, 0.2],
        "frame": "world",
        "condition": "cracked",
    },
]

MANUALS = [
    {"part_type": "flange", "torque_spec_nm": 45.0, "inspection_interval_days": 90},
    {"part_type": "valve", "torque_spec_nm": 30.0, "inspection_interval_days": 180},
    {"part_type": "pipe_joint", "torque_spec_nm": 55.0, "inspection_interval_days": 60},
]


def init_s4_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE assets (
            asset_id TEXT PRIMARY KEY, name TEXT, position_json TEXT,
            frame TEXT, condition TEXT
        );
        CREATE TABLE manuals (
            part_type TEXT PRIMARY KEY, torque_spec_nm REAL, inspection_interval_days INTEGER
        );
        CREATE TABLE workorders_generated (
            wo_id TEXT PRIMARY KEY, asset_id TEXT, description TEXT, created_at REAL
        );
    """)
    for a in ASSETS:
        conn.execute(
            "INSERT INTO assets VALUES (?,?,?,?,?)",
            (a["asset_id"], a["name"], json.dumps(a["position"]), a["frame"], a["condition"]),
        )
    for m in MANUALS:
        conn.execute(
            "INSERT INTO manuals VALUES (?,?,?)",
            (m["part_type"], m["torque_spec_nm"], m["inspection_interval_days"]),
        )
    conn.commit()
    return conn


def resolve_asset_to_position(conn: sqlite3.Connection, asset_id: str) -> list[float] | None:
    row = conn.execute("SELECT position_json FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
    return json.loads(row[0]) if row else None  # type: ignore[index]
