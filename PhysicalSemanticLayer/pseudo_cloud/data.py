"""Pseudo-cloud — local business data for document grounding tests.

Contains inventory, SOPs, and work orders in SQLite.
This simulates the cloud-side business data that agents interact with.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "psl_bench.db"

# Sample data for the cross-cutting task (PROJECT.md §6.5)
INVENTORY = [
    # Original items
    {
        "item_id": "gear_blue_001",
        "name": "Blue Gear",
        "bin": "C",
        "status": "defective",
        "mass_kg": 0.1,
        "handling": "normal",
    },
    {
        "item_id": "gear_red_002",
        "name": "Red Gear",
        "bin": "A",
        "status": "ok",
        "mass_kg": 0.1,
        "handling": "normal",
    },
    {
        "item_id": "bolt_003",
        "name": "M6 Bolt",
        "bin": "B",
        "status": "ok",
        "mass_kg": 0.01,
        "handling": "normal",
    },
    # Expanded inventory — gears
    {
        "item_id": "GEAR-001",
        "name": "Spur Gear 32T",
        "bin": "Bin_A",
        "status": "ok",
        "mass_kg": 0.15,
        "handling": "normal",
    },
    {
        "item_id": "GEAR-002",
        "name": "Bevel Gear 24T",
        "bin": "Bin_A",
        "status": "ok",
        "mass_kg": 0.12,
        "handling": "normal",
    },
    {
        "item_id": "GEAR-003",
        "name": "Worm Gear M2",
        "bin": "Bin_A",
        "status": "defective",
        "mass_kg": 0.18,
        "handling": "normal",
    },
    # Bolts
    {
        "item_id": "BOLT-001",
        "name": "M8 Hex Bolt",
        "bin": "Bin_B",
        "status": "ok",
        "mass_kg": 0.02,
        "handling": "normal",
    },
    {
        "item_id": "BOLT-002",
        "name": "M10 Socket Bolt",
        "bin": "Bin_B",
        "status": "ok",
        "mass_kg": 0.03,
        "handling": "normal",
    },
    {
        "item_id": "BOLT-003",
        "name": "M4 Flanged Bolt",
        "bin": "Bin_B",
        "status": "ok",
        "mass_kg": 0.008,
        "handling": "normal",
    },
    # Bearings
    {
        "item_id": "BRG-001",
        "name": "Ball Bearing 6205",
        "bin": "Bin_C",
        "status": "ok",
        "mass_kg": 0.11,
        "handling": "fragile",
    },
    {
        "item_id": "BRG-002",
        "name": "Roller Bearing NJ204",
        "bin": "Bin_C",
        "status": "ok",
        "mass_kg": 0.14,
        "handling": "fragile",
    },
    # Connectors
    {
        "item_id": "CONN-001",
        "name": "DB25 Connector",
        "bin": "Bin_D",
        "status": "ok",
        "mass_kg": 0.02,
        "handling": "fragile",
    },
    {
        "item_id": "CONN-002",
        "name": "M12 Industrial Connector",
        "bin": "Bin_D",
        "status": "ok",
        "mass_kg": 0.04,
        "handling": "fragile",
    },
    # PCBs
    {
        "item_id": "PCB-001",
        "name": "Motor Driver PCB v3",
        "bin": "Bin_E",
        "status": "ok",
        "mass_kg": 0.05,
        "handling": "fragile",
    },
    {
        "item_id": "PCB-002",
        "name": "Sensor Interface PCB",
        "bin": "Bin_E",
        "status": "defective",
        "mass_kg": 0.04,
        "handling": "fragile",
    },
    # Sensors
    {
        "item_id": "SENS-001",
        "name": "Proximity Sensor IR",
        "bin": "Bin_F",
        "status": "ok",
        "mass_kg": 0.03,
        "handling": "fragile",
    },
    {
        "item_id": "SENS-002",
        "name": "Force Torque Sensor",
        "bin": "Bin_F",
        "status": "ok",
        "mass_kg": 0.25,
        "handling": "fragile",
    },
    # Cables
    {
        "item_id": "CABLE-001",
        "name": "Ethernet Cat6 3m",
        "bin": "Bin_G",
        "status": "ok",
        "mass_kg": 0.08,
        "handling": "normal",
    },
    {
        "item_id": "CABLE-002",
        "name": "Power Cable 12AWG 2m",
        "bin": "Bin_G",
        "status": "ok",
        "mass_kg": 0.12,
        "handling": "normal",
    },
    # Brackets
    {
        "item_id": "BRKT-001",
        "name": "L-Bracket Aluminium",
        "bin": "Bin_H",
        "status": "ok",
        "mass_kg": 0.06,
        "handling": "normal",
    },
    {
        "item_id": "BRKT-002",
        "name": "U-Bracket Steel",
        "bin": "Bin_H",
        "status": "ok",
        "mass_kg": 0.09,
        "handling": "normal",
    },
    # Hazardous
    {
        "item_id": "HAZ-001",
        "name": "Lithium Cell 18650",
        "bin": "Bin_E",
        "status": "ok",
        "mass_kg": 0.045,
        "handling": "hazardous",
    },
    {
        "item_id": "HAZ-002",
        "name": "Soldering Flux 50ml",
        "bin": "Bin_G",
        "status": "ok",
        "mass_kg": 0.06,
        "handling": "hazardous",
    },
    # Zero-quantity placeholder
    {
        "item_id": "EMPTY-001",
        "name": "Depleted Fuse Pack",
        "bin": "Bin_H",
        "status": "depleted",
        "mass_kg": 0.0,
        "handling": "normal",
    },
]

BIN_LOCATIONS = {
    "A": {"frame": "world", "position": [0.3, -0.3, 0.45], "description": "Left front bin"},
    "B": {"frame": "world", "position": [0.3, 0.0, 0.45], "description": "Center bin"},
    "C": {"frame": "world", "position": [0.3, 0.3, 0.45], "description": "Right front bin"},
    "QA_TRAY": {
        "frame": "world",
        "position": [0.7, 0.0, 0.45],
        "description": "QA inspection tray",
    },
    "Bin_A": {"frame": "world", "position": [0.3, -0.6, 0.45], "description": "Bin A — gears"},
    "Bin_B": {"frame": "world", "position": [0.3, -0.4, 0.45], "description": "Bin B — bolts"},
    "Bin_C": {"frame": "world", "position": [0.3, -0.2, 0.45], "description": "Bin C — bearings"},
    "Bin_D": {"frame": "world", "position": [0.3, 0.0, 0.55], "description": "Bin D — connectors"},
    "Bin_E": {"frame": "world", "position": [0.3, 0.2, 0.55], "description": "Bin E — PCBs/cells"},
    "Bin_F": {"frame": "world", "position": [0.3, 0.4, 0.55], "description": "Bin F — sensors"},
    "Bin_G": {"frame": "world", "position": [0.3, 0.6, 0.55], "description": "Bin G — cables"},
    "Bin_H": {"frame": "world", "position": [0.3, 0.8, 0.55], "description": "Bin H — brackets"},
    "STAGING": {"frame": "world", "position": [0.5, 0.0, 0.40], "description": "Staging area"},
    "REJECT_BIN": {"frame": "world", "position": [0.8, -0.4, 0.40], "description": "Reject bin"},
    "OUTPUT": {"frame": "world", "position": [0.8, 0.4, 0.40], "description": "Output conveyor"},
}

WORK_ORDERS = [
    {
        "work_order_id": "WO-42",
        "title": "Retrieve defective blue gear from Bin C",
        "description": "The blue gear in Bin C has been flagged as defective. "
        "Retrieve it and place it in the QA tray for inspection.",
        "source_bin": "C",
        "target_location": "QA_TRAY",
        "item_id": "gear_blue_001",
        "status": "open",
        "priority": "high",
    },
    {
        "work_order_id": "WO-43",
        "title": "Stage spur gears for assembly",
        "description": "Move spur gears from Bin A to staging area for Line 2 assembly.",
        "source_bin": "Bin_A",
        "target_location": "STAGING",
        "item_id": "GEAR-001",
        "status": "pending",
        "priority": "normal",
    },
    {
        "work_order_id": "WO-44",
        "title": "Replace defective PCB",
        "description": "Sensor interface PCB in Bin E is defective. Move to reject bin.",
        "source_bin": "Bin_E",
        "target_location": "REJECT_BIN",
        "item_id": "PCB-002",
        "status": "in_progress",
        "priority": "high",
    },
    {
        "work_order_id": "WO-45",
        "title": "Ship bearings to output",
        "description": "Ball bearings order fulfilled. Move from Bin C to output conveyor.",
        "source_bin": "Bin_C",
        "target_location": "OUTPUT",
        "item_id": "BRG-001",
        "status": "completed",
        "priority": "normal",
    },
    {
        "work_order_id": "WO-46",
        "title": "Inspect hazardous lithium cells",
        "description": "Lithium cells require safety inspection before shipment.",
        "source_bin": "Bin_E",
        "target_location": "QA_TRAY",
        "item_id": "HAZ-001",
        "status": "failed",
        "priority": "critical",
    },
    {
        "work_order_id": "WO-47",
        "title": "Return connector batch (cancelled order)",
        "description": "Customer cancelled order for DB25 connectors. Return to storage.",
        "source_bin": "STAGING",
        "target_location": "Bin_D",
        "item_id": "CONN-001",
        "status": "cancelled",
        "priority": "low",
    },
]

SOPS = [
    {
        "sop_id": "SOP-PICK-001",
        "title": "Standard Pick Procedure",
        "steps": [
            "Identify target bin location from work order",
            "Move robot end-effector above the bin",
            "Lower end-effector to grasp height",
            "Close gripper on target object",
            "Lift object clear of bin",
            "Move to target location",
            "Lower and release object",
            "Confirm placement",
        ],
    },
    {
        "sop_id": "SOP-HAZMAT-002",
        "title": "Hazardous Material Handling Procedure",
        "steps": [
            "Verify operator has hazmat certification",
            "Don appropriate PPE (gloves, eye protection)",
            "Confirm containment tray is in place at target",
            "Approach bin at reduced speed (max 0.1 m/s)",
            "Grasp item with padded gripper",
            "Lift slowly — no sudden acceleration",
            "Transport to target with collision avoidance enabled",
            "Place item gently on containment tray",
            "Verify no leakage or damage",
            "Log handling event with timestamp and operator ID",
        ],
    },
    {
        "sop_id": "SOP-QA-003",
        "title": "Quality Assurance Inspection Procedure",
        "steps": [
            "Receive item at QA tray",
            "Scan barcode / RFID tag",
            "Visual inspection under magnification",
            "Dimensional check against spec sheet",
            "Record pass/fail verdict",
            "If pass: route to OUTPUT",
            "If fail: route to REJECT_BIN and create exception report",
        ],
    },
    {
        "sop_id": "SOP-RESTOCK-004",
        "title": "Bin Restock Procedure",
        "steps": [
            "Check inventory level for target bin",
            "If quantity below threshold, generate restock request",
            "Receive replenishment pallet at STAGING",
            "Verify item IDs match restock request",
            "Transfer items from STAGING to target bin",
            "Update inventory count in system",
        ],
    },
]

PERMISSIONS = [
    # operator — limited to read and execute
    {"role": "operator", "resource": "inventory", "action": "read"},
    {"role": "operator", "resource": "work_orders", "action": "read"},
    {"role": "operator", "resource": "work_orders", "action": "execute"},
    {"role": "operator", "resource": "sops", "action": "read"},
    # supervisor — full access
    {"role": "supervisor", "resource": "inventory", "action": "read"},
    {"role": "supervisor", "resource": "inventory", "action": "write"},
    {"role": "supervisor", "resource": "work_orders", "action": "read"},
    {"role": "supervisor", "resource": "work_orders", "action": "write"},
    {"role": "supervisor", "resource": "work_orders", "action": "execute"},
    {"role": "supervisor", "resource": "sops", "action": "read"},
    {"role": "supervisor", "resource": "sops", "action": "write"},
    # qa_inspector — read all, write work_orders (verdicts)
    {"role": "qa_inspector", "resource": "inventory", "action": "read"},
    {"role": "qa_inspector", "resource": "work_orders", "action": "read"},
    {"role": "qa_inspector", "resource": "work_orders", "action": "write"},
    {"role": "qa_inspector", "resource": "sops", "action": "read"},
]


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Initialize the pseudo-cloud SQLite database with sample data.

    Args:
        db_path: Path to database file. Uses in-memory if None.

    Returns:
        SQLite connection.
    """
    path = str(db_path) if db_path else ":memory:"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS inventory (
            item_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            bin TEXT NOT NULL,
            status TEXT NOT NULL,
            mass_kg REAL NOT NULL,
            handling TEXT NOT NULL DEFAULT 'normal'
        );
        CREATE TABLE IF NOT EXISTS bin_locations (
            bin_id TEXT PRIMARY KEY,
            frame TEXT NOT NULL,
            position_json TEXT NOT NULL,
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS work_orders (
            work_order_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            source_bin TEXT,
            target_location TEXT,
            item_id TEXT,
            status TEXT DEFAULT 'open',
            priority TEXT DEFAULT 'normal'
        );
        CREATE TABLE IF NOT EXISTS sops (
            sop_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            steps_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS permissions (
            role TEXT NOT NULL,
            resource TEXT NOT NULL,
            action TEXT NOT NULL,
            PRIMARY KEY (role, resource, action)
        );
    """)

    for item in INVENTORY:
        conn.execute(
            "INSERT OR REPLACE INTO inventory VALUES (?, ?, ?, ?, ?, ?)",
            (
                item["item_id"],
                item["name"],
                item["bin"],
                item["status"],
                item["mass_kg"],
                item.get("handling", "normal"),
            ),
        )
    for bin_id, loc in BIN_LOCATIONS.items():
        conn.execute(
            "INSERT OR REPLACE INTO bin_locations VALUES (?, ?, ?, ?)",
            (bin_id, loc["frame"], json.dumps(loc["position"]), loc["description"]),
        )
    for wo in WORK_ORDERS:
        conn.execute(
            "INSERT OR REPLACE INTO work_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                wo["work_order_id"],
                wo["title"],
                wo["description"],
                wo["source_bin"],
                wo["target_location"],
                wo["item_id"],
                wo["status"],
                wo["priority"],
            ),
        )
    for sop in SOPS:
        conn.execute(
            "INSERT OR REPLACE INTO sops VALUES (?, ?, ?)",
            (sop["sop_id"], sop["title"], json.dumps(sop["steps"])),
        )
    for perm in PERMISSIONS:
        conn.execute(
            "INSERT OR REPLACE INTO permissions VALUES (?, ?, ?)",
            (perm["role"], perm["resource"], perm["action"]),
        )
    conn.commit()
    return conn


def query_work_order(conn: sqlite3.Connection, wo_id: str) -> dict[str, object] | None:
    """Query a work order by ID."""
    row = conn.execute("SELECT * FROM work_orders WHERE work_order_id = ?", (wo_id,)).fetchone()
    return dict(row) if row else None


def query_inventory(conn: sqlite3.Connection, item_id: str) -> dict[str, object] | None:
    """Query an inventory item by ID."""
    row = conn.execute("SELECT * FROM inventory WHERE item_id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


def resolve_bin_to_position(conn: sqlite3.Connection, bin_id: str) -> list[float] | None:
    """Resolve a bin name to its physical position (document → physical anchoring)."""
    row = conn.execute(
        "SELECT position_json FROM bin_locations WHERE bin_id = ?", (bin_id,)
    ).fetchone()
    if row:
        return json.loads(row["position_json"])  # type: ignore[index]
    return None


def query_sop(conn: sqlite3.Connection, sop_id: str) -> dict[str, object] | None:
    """Query an SOP by ID."""
    row = conn.execute("SELECT * FROM sops WHERE sop_id = ?", (sop_id,)).fetchone()
    if row:
        d = dict(row)
        d["steps"] = json.loads(d["steps_json"])  # type: ignore[arg-type]
        del d["steps_json"]
        return d
    return None


def check_permission(conn: sqlite3.Connection, role: str, resource: str, action: str) -> bool:
    """Check if a role has permission for an action on a resource."""
    cursor = conn.execute(
        "SELECT COUNT(*) FROM permissions WHERE role = ? AND resource = ? AND action = ?",
        (role, resource, action),
    )
    return cursor.fetchone()[0] > 0  # type: ignore[index]
