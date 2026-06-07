"""S6 pseudo-cloud: Material values, compliance classifications, manifest generation."""

from __future__ import annotations

import sqlite3

MATERIALS = [
    {"material": "pcb", "value_per_kg": 15.0, "hazardous": False, "category": "electronics"},
    {"material": "lithium", "value_per_kg": 25.0, "hazardous": True, "category": "battery"},
    {"material": "copper", "value_per_kg": 8.0, "hazardous": False, "category": "metal"},
    {"material": "abs", "value_per_kg": 0.5, "hazardous": False, "category": "plastic"},
    {"material": "aluminum", "value_per_kg": 2.0, "hazardous": False, "category": "metal"},
    {"material": "glass", "value_per_kg": 0.1, "hazardous": False, "category": "inert"},
]


def init_s6_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE materials (
            material TEXT PRIMARY KEY, value_per_kg REAL,
            hazardous INTEGER, category TEXT
        );
        CREATE TABLE manifest (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_id TEXT, material TEXT, mass_kg REAL,
            disposition TEXT, timestamp REAL
        );
    """)
    for m in MATERIALS:
        conn.execute(
            "INSERT INTO materials VALUES (?,?,?,?)",
            (m["material"], m["value_per_kg"], int(m["hazardous"]), m["category"]),
        )
    conn.commit()
    return conn


def get_material_value(conn: sqlite3.Connection, material: str) -> float:
    row = conn.execute(
        "SELECT value_per_kg FROM materials WHERE material=?", (material,)
    ).fetchone()
    return float(row[0]) if row else 0.0  # type: ignore[index]
