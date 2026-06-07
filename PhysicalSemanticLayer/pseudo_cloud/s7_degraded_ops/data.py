"""S7 pseudo-cloud: Floorplan and degradation profiles."""

from __future__ import annotations

import json
import sqlite3

FLOORPLAN_POIS = [
    {"poi_id": "entry", "position": [0.0, 0.0, 0.0], "label": "Entry Point"},
    {"poi_id": "staging", "position": [1.0, 0.0, 0.0], "label": "Staging Area"},
    {"poi_id": "workstation", "position": [2.0, 0.5, 0.0], "label": "Workstation"},
    {"poi_id": "exit", "position": [3.0, 0.0, 0.0], "label": "Exit"},
]

DEGRADATION_PROFILES = [
    {"profile": "nominal", "delay_s": 0.0, "jitter_s": 0.0, "clock_skew_s": 0.0},
    {"profile": "mild", "delay_s": 0.05, "jitter_s": 0.01, "clock_skew_s": 0.01},
    {"profile": "moderate", "delay_s": 0.2, "jitter_s": 0.05, "clock_skew_s": 0.1},
    {"profile": "severe", "delay_s": 1.0, "jitter_s": 0.2, "clock_skew_s": 0.5},
]


def init_s7_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE floorplan (poi_id TEXT PRIMARY KEY, position_json TEXT, label TEXT);
        CREATE TABLE degradation_profiles (
            profile TEXT PRIMARY KEY, delay_s REAL, jitter_s REAL, clock_skew_s REAL
        );
    """)
    for p in FLOORPLAN_POIS:
        conn.execute(
            "INSERT INTO floorplan VALUES (?,?,?)",
            (p["poi_id"], json.dumps(p["position"]), p["label"]),
        )
    for d in DEGRADATION_PROFILES:
        conn.execute(
            "INSERT INTO degradation_profiles VALUES (?,?,?,?)",
            (d["profile"], d["delay_s"], d["jitter_s"], d["clock_skew_s"]),
        )
    conn.commit()
    return conn
