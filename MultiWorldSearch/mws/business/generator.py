"""Synthetic business data generator for Scenario 1 (maintenance handoff)."""

from __future__ import annotations

from mws.business.schemas import CMWorkOrder, InventoryItem, MaintenanceLog, SOPDocument
from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality


def generate_maintenance_data(seed: int = 0) -> dict[str, list]:
    """Generate synthetic business data for the maintenance handoff scenario.

    Returns dict with keys: work_orders, inventory, sops, maintenance_logs.
    """
    work_orders = [
        CMWorkOrder(
            work_order_id="WO-001",
            equipment_id="CONV-01",
            description="Conveyor motor overheating. Temperature exceeded 80C threshold.",
            priority=2,
            status="open",
            assigned_to="maintenance_robot",
            created_at=1700000000.0,
        ),
        CMWorkOrder(
            work_order_id="WO-002",
            equipment_id="CONV-01",
            description="Scheduled preventive maintenance for conveyor drive motor.",
            priority=3,
            status="closed",
            assigned_to="tech-A",
            created_at=1699900000.0,
            completed_at=1699910000.0,
        ),
    ]

    inventory = [
        InventoryItem(
            item_id="PART-001",
            name="Conveyor motor bearing",
            location="shelf-A3",
            quantity=5,
            min_stock=2,
        ),
        InventoryItem(
            item_id="PART-002",
            name="Thermal paste (industrial grade)",
            location="shelf-B1",
            quantity=10,
            unit="tubes",
            min_stock=3,
        ),
        InventoryItem(
            item_id="PART-003",
            name="Motor cooling fan assembly",
            location="shelf-A3",
            quantity=2,
            min_stock=1,
        ),
    ]

    sops = [
        SOPDocument(
            sop_id="SOP-CONV-MOTOR-01",
            title="Conveyor Motor Overheating — Diagnosis and Repair",
            equipment_type="conveyor_motor",
            content=(
                "1. Power off conveyor and lock out/tag out.\n"
                "2. Measure motor temperature with IR thermometer.\n"
                "3. If >85C: check bearing condition, lubrication, cooling fan.\n"
                "4. Replace bearing if worn (part: PART-001).\n"
                "5. Apply thermal paste if needed (part: PART-002).\n"
                "6. Verify cooling fan operation (part: PART-003 if faulty).\n"
                "7. Restart and monitor for 30 minutes."
            ),
            safety_notes=[
                "Ensure LOTO before any physical contact",
                "Wear heat-resistant gloves",
                "Do not restart if temperature >90C after repair",
            ],
        ),
    ]

    maintenance_logs = [
        MaintenanceLog(
            log_id="LOG-001",
            equipment_id="CONV-01",
            action="bearing_replacement",
            technician="tech-A",
            timestamp=1699910000.0,
            notes="Replaced worn bearing. Motor temperature dropped to 45C after repair.",
            parts_used=["PART-001"],
        ),
        MaintenanceLog(
            log_id="LOG-002",
            equipment_id="CONV-01",
            action="preventive_inspection",
            technician="tech-B",
            timestamp=1699800000.0,
            notes="Visual inspection. No issues found. Bearing showing minor wear.",
        ),
    ]

    return {
        "work_orders": work_orders,
        "inventory": inventory,
        "sops": sops,
        "maintenance_logs": maintenance_logs,
    }


def business_data_to_atoms(data: dict[str, list], seed: int = 0) -> list[ExperienceAtom]:
    """Convert synthetic business data into experience atoms."""
    atoms: list[ExperienceAtom] = []
    # Default position (office/system area)
    default_coord = SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=1700000000.0)

    for wo in data["work_orders"]:
        coord = SpatiotemporalCoord(x=2.0, y=0.0, z=0.5, timestamp=wo.created_at)
        atom = ExperienceAtom(
            modality=Modality.STRUCTURED_RECORD,
            coord=coord,
            text_summary=f"Work Order {wo.work_order_id}: {wo.description}",
            tags=["cmms", "work_order", wo.equipment_id, f"priority-{wo.priority}"],
            structured_fields={
                "equipment_id": wo.equipment_id,
                "priority": wo.priority,
                "status": wo.status,
                "work_order_id": wo.work_order_id,
            },
            payload=wo.model_dump(),
        )
        atom.provenance.add("cmms_system", "system", "created")
        atoms.append(atom)

    for item in data["inventory"]:
        atom = ExperienceAtom(
            modality=Modality.STRUCTURED_RECORD,
            coord=default_coord,
            text_summary=f"Inventory: {item.name} — {item.quantity} {item.unit} at {item.location}",
            tags=["wms", "inventory", item.item_id],
            structured_fields={
                "item_id": item.item_id,
                "quantity": item.quantity,
                "location": item.location,
            },
            payload=item.model_dump(),
        )
        atom.provenance.add("wms_system", "system", "created")
        atoms.append(atom)

    for sop in data["sops"]:
        atom = ExperienceAtom(
            modality=Modality.SOP,
            coord=default_coord,
            text_summary=f"SOP: {sop.title}\n{sop.content}",
            tags=["sop", sop.equipment_type, sop.sop_id],
            structured_fields={"sop_id": sop.sop_id, "equipment_type": sop.equipment_type},
            payload=sop.model_dump(),
        )
        atom.provenance.add("document_store", "system", "created")
        atoms.append(atom)

    for log in data["maintenance_logs"]:
        coord = SpatiotemporalCoord(x=2.0, y=0.0, z=0.5, timestamp=log.timestamp)
        atom = ExperienceAtom(
            modality=Modality.STRUCTURED_RECORD,
            coord=coord,
            text_summary=(
                f"Maintenance log: {log.action} on {log.equipment_id} by {log.technician}. "
                f"{log.notes}"
            ),
            tags=["maintenance_log", log.equipment_id, log.action],
            structured_fields={
                "equipment_id": log.equipment_id,
                "action": log.action,
                "technician": log.technician,
            },
            payload=log.model_dump(),
        )
        atom.provenance.add("cmms_system", "system", "created")
        atoms.append(atom)

    return atoms
