"""S5 pseudo-cloud: Pharmaceutical formulary, expiry tracking, audit log."""

from __future__ import annotations

import sqlite3

FORMULARY = [
    {
        "drug_id": "DRG-001",
        "name": "Morphine 10mg",
        "regulatory_class": "schedule_II",
        "max_dose_mg": 10.0,
        "storage_temp_c": 25.0,
    },
    {
        "drug_id": "DRG-002",
        "name": "Saline 0.9%",
        "regulatory_class": "none",
        "max_dose_mg": 9999.0,
        "storage_temp_c": 25.0,
    },
    {
        "drug_id": "DRG-003",
        "name": "Insulin 100U",
        "regulatory_class": "prescription",
        "max_dose_mg": 100.0,
        "storage_temp_c": 4.0,
    },
]

EXPIRY = [
    {"vial_id": "V-001", "drug_id": "DRG-001", "expiry_days_remaining": 30},
    {"vial_id": "V-002", "drug_id": "DRG-002", "expiry_days_remaining": 365},
    {"vial_id": "V-003", "drug_id": "DRG-003", "expiry_days_remaining": -5},  # EXPIRED
    {"vial_id": "V-004", "drug_id": "DRG-001", "expiry_days_remaining": 90},
]


def init_s5_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE formulary (
            drug_id TEXT PRIMARY KEY, name TEXT, regulatory_class TEXT,
            max_dose_mg REAL, storage_temp_c REAL
        );
        CREATE TABLE expiry (
            vial_id TEXT PRIMARY KEY, drug_id TEXT, expiry_days_remaining INTEGER
        );
        CREATE TABLE audit_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            vial_id TEXT, action TEXT, result TEXT, timestamp REAL
        );
    """)
    for f in FORMULARY:
        conn.execute(
            "INSERT INTO formulary VALUES (?,?,?,?,?)",
            (
                f["drug_id"],
                f["name"],
                f["regulatory_class"],
                f["max_dose_mg"],
                f["storage_temp_c"],
            ),
        )
    for e in EXPIRY:
        conn.execute(
            "INSERT INTO expiry VALUES (?,?,?)",
            (e["vial_id"], e["drug_id"], e["expiry_days_remaining"]),
        )
    conn.commit()
    return conn


def check_expiry(conn: sqlite3.Connection, vial_id: str) -> bool:
    """Returns True if vial is NOT expired."""
    row = conn.execute(
        "SELECT expiry_days_remaining FROM expiry WHERE vial_id=?", (vial_id,)
    ).fetchone()
    return row is not None and int(row[0]) > 0
