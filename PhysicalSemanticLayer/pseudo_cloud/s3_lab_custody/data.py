"""S3 pseudo-cloud: Lab samples, SOP protocols, and custody log schema."""

from __future__ import annotations

import json
import sqlite3

SAMPLES = [
    {"sample_id": "SPL-001", "type": "blood", "patient": "P-100", "status": "pending"},
    {"sample_id": "SPL-002", "type": "urine", "patient": "P-101", "status": "pending"},
    {"sample_id": "SPL-003", "type": "blood", "patient": "P-102", "status": "pending"},
]

LAB_SOP = {
    "sop_id": "SOP-LAB-001",
    "title": "Standard Sample Processing",
    "steps": [
        "Scan sample barcode at intake",
        "Transfer to liquid handler rack",
        "Dispense aliquot into test plate",
        "Transport plate to analyzer",
        "Record result and update custody log",
    ],
}


def init_s3_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE samples (
            sample_id TEXT PRIMARY KEY, type TEXT, patient TEXT, status TEXT
        );
        CREATE TABLE custody_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id TEXT, step TEXT, robot_id TEXT,
            timestamp REAL, confidence REAL, provenance_json TEXT
        );
        CREATE TABLE sop (sop_id TEXT PRIMARY KEY, title TEXT, steps_json TEXT);
    """)
    for s in SAMPLES:
        conn.execute(
            "INSERT INTO samples VALUES (?,?,?,?)",
            (s["sample_id"], s["type"], s["patient"], s["status"]),
        )
    conn.execute(
        "INSERT INTO sop VALUES (?,?,?)",
        (LAB_SOP["sop_id"], LAB_SOP["title"], json.dumps(LAB_SOP["steps"])),
    )
    conn.commit()
    return conn


def log_custody_event(
    conn: sqlite3.Connection,
    sample_id: str,
    step: str,
    robot_id: str,
    timestamp: float,
    confidence: float,
    provenance_json: str,
) -> None:
    conn.execute(
        "INSERT INTO custody_log (sample_id, step, robot_id, timestamp, confidence, provenance_json) VALUES (?,?,?,?,?,?)",
        (sample_id, step, robot_id, timestamp, confidence, provenance_json),
    )
    conn.commit()


def get_custody_chain(conn: sqlite3.Connection, sample_id: str) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT * FROM custody_log WHERE sample_id=? ORDER BY timestamp", (sample_id,)
    ).fetchall()
    return [dict(r) for r in rows]
