"""S2 pseudo-cloud: Manufacturing recipes and equipment limits."""

from __future__ import annotations

import json
import sqlite3

RECIPES = [
    {
        "recipe_id": "R-100",
        "product": "Widget-A",
        "joints_target": [0.5, -0.3, 0.2, -1.5, 0.0, 1.0, 0.3],
        "torque_max": [80, 80, 80, 80, 20, 20, 20],
        "tolerance_m": 0.002,
        "feasible": True,
    },
    {
        "recipe_id": "R-101",
        "product": "Widget-B",
        "joints_target": [1.0, 0.5, -0.5, -2.0, 1.0, 2.0, -0.5],
        "torque_max": [80, 80, 80, 80, 20, 20, 20],
        "tolerance_m": 0.001,
        "feasible": True,
    },
    {
        "recipe_id": "R-999",
        "product": "Impossible-Widget",
        "joints_target": [5.0, 5.0, 5.0, 0.0, 5.0, 5.0, 5.0],
        "torque_max": [200, 200, 200, 200, 200, 200, 200],
        "tolerance_m": 0.0001,
        "feasible": False,
    },
]

EQUIPMENT = [
    {"robot_id": "robot_a", "joint_limit_lower": -2.9, "joint_limit_upper": 2.9, "max_torque": 87},
    {"robot_id": "robot_b", "joint_limit_lower": -2.9, "joint_limit_upper": 2.9, "max_torque": 87},
]


def init_s2_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE recipes (
            recipe_id TEXT PRIMARY KEY, product TEXT, joints_target_json TEXT,
            torque_max_json TEXT, tolerance_m REAL, feasible INTEGER
        );
        CREATE TABLE equipment (
            robot_id TEXT PRIMARY KEY, joint_limit_lower REAL,
            joint_limit_upper REAL, max_torque REAL
        );
    """)
    for r in RECIPES:
        conn.execute(
            "INSERT INTO recipes VALUES (?,?,?,?,?,?)",
            (
                r["recipe_id"],
                r["product"],
                json.dumps(r["joints_target"]),
                json.dumps(r["torque_max"]),
                r["tolerance_m"],
                int(r["feasible"]),
            ),
        )
    for e in EQUIPMENT:
        conn.execute(
            "INSERT INTO equipment VALUES (?,?,?,?)",
            (e["robot_id"], e["joint_limit_lower"], e["joint_limit_upper"], e["max_torque"]),
        )
    conn.commit()
    return conn


def get_recipe(conn: sqlite3.Connection, recipe_id: str) -> dict[str, object] | None:
    row = conn.execute("SELECT * FROM recipes WHERE recipe_id=?", (recipe_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["joints_target"] = json.loads(d["joints_target_json"])  # type: ignore[arg-type]
    d["torque_max"] = json.loads(d["torque_max_json"])  # type: ignore[arg-type]
    return d
