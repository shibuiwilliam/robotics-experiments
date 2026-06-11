"""Business data for Scenario 1: maintenance handoff.

Generates: CMMS history for pump_07, ERP parts inventory, SOP with torque specs,
technician shift schedule.
"""

from __future__ import annotations

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality


def generate_s1_business_atoms(seed: int = 0) -> list[ExperienceAtom]:
    """Generate scenario 1 business data as atoms."""
    atoms: list[ExperienceAtom] = []
    pump_coord = SpatiotemporalCoord(x=2.0, y=1.0, z=0.4, timestamp=1700000000.0)
    default_coord = SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=1700000000.0)

    # CMMS work order history for pump_07
    atoms.append(
        _atom(
            modality=Modality.STRUCTURED_RECORD,
            coord=pump_coord,
            text="Work order WO-101: pump_07 bearing replacement. Vibration exceeded threshold. "
            "Replaced bearing, applied lubricant. Downtime 4 hours.",
            tags=["cmms", "work_order", "pump_07", "maintenance", "bearing"],
            fields={
                "equipment_id": "pump_07",
                "work_order_id": "WO-101",
                "action": "bearing_replacement",
            },
            source_id="cmms_system",
            source_type="system",
        )
    )
    atoms.append(
        _atom(
            modality=Modality.STRUCTURED_RECORD,
            coord=SpatiotemporalCoord(x=2.0, y=1.0, z=0.4, timestamp=1699900000.0),
            text="Work order WO-098: pump_07 preventive maintenance. Visual inspection, lubrication. No issues.",
            tags=["cmms", "work_order", "pump_07", "maintenance", "preventive"],
            fields={"equipment_id": "pump_07", "work_order_id": "WO-098", "action": "preventive"},
            source_id="cmms_system",
            source_type="system",
        )
    )

    # ERP parts inventory
    atoms.append(
        _atom(
            modality=Modality.STRUCTURED_RECORD,
            coord=default_coord,
            text="Inventory: Pump bearing assembly P-BEAR-07, 3 units at shelf-C2. Min stock 2.",
            tags=["erp", "inventory", "pump_07", "bearing", "parts"],
            fields={
                "part_id": "P-BEAR-07",
                "equipment_id": "pump_07",
                "quantity": 3,
                "location": "shelf-C2",
            },
            source_id="erp_system",
            source_type="system",
        )
    )
    atoms.append(
        _atom(
            modality=Modality.STRUCTURED_RECORD,
            coord=default_coord,
            text="Inventory: Seal kit SK-07, 5 units at shelf-C2.",
            tags=["erp", "inventory", "pump_07", "seal", "parts"],
            fields={
                "part_id": "SK-07",
                "equipment_id": "pump_07",
                "quantity": 5,
                "location": "shelf-C2",
            },
            source_id="erp_system",
            source_type="system",
        )
    )

    # SOP with torque specs
    atoms.append(
        _atom(
            modality=Modality.SOP,
            coord=default_coord,
            text="SOP-PUMP-07: Pump maintenance procedure.\n"
            "1. Isolate and lock-out pump_07.\n"
            "2. Close valve_03 (loosen handle counter-clockwise).\n"
            "3. Remove pump casing (torque spec: 45 Nm).\n"
            "4. Inspect bearing for wear. Replace if runout > 0.05mm.\n"
            "5. Apply bearing grease (spec: lithium EP2).\n"
            "6. Reassemble casing (torque: 45 Nm).\n"
            "7. Open valve_03. Start pump. Monitor vibration for 30 min.",
            tags=["sop", "pump_07", "maintenance", "valve_03", "torque"],
            fields={"sop_id": "SOP-PUMP-07", "equipment_type": "pump", "equipment_id": "pump_07"},
            source_id="document_store",
            source_type="system",
        )
    )

    # Technician shift schedule
    atoms.append(
        _atom(
            modality=Modality.STRUCTURED_RECORD,
            coord=default_coord,
            text="Shift schedule: Technician T-Yamada on call 08:00-20:00. "
            "Technician T-Tanaka on call 20:00-08:00. Both certified for pump maintenance.",
            tags=["shift", "personnel", "maintenance", "pump_07"],
            fields={"shift_id": "SHIFT-001", "primary": "T-Yamada", "backup": "T-Tanaka"},
            source_id="hr_system",
            source_type="system",
        )
    )

    return atoms


def generate_s1_skill_demo(seed: int = 0) -> ExperienceAtom:
    """Generate the pre-existing VLA skill demo: 'loosen valve_03'.

    This was taught by a different robot in the past and should be
    retrievable by the MobileManipulator for the current task.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    trajectory = rng.standard_normal((10, 6)).tolist()

    coord = SpatiotemporalCoord(x=2.5, y=1.0, z=0.3, timestamp=1699800000.0)
    atom = ExperienceAtom(
        modality=Modality.SKILL_DEMO,
        coord=coord,
        text_summary="VLA skill demonstration: loosen valve_03 handle. "
        "Counter-clockwise rotation, 10-step trajectory. "
        "Taught by patrol_robot on pump_07 maintenance task.",
        tags=["skill_demo", "loosen_valve", "valve_03", "pump_07", "maintenance"],
        entity_id="valve_03",
        structured_fields={
            "skill_name": "loosen_valve",
            "equipment_id": "valve_03",
            "related_equipment": "pump_07",
            "trajectory_length": 10,
        },
        payload={"trajectory": trajectory},
    )
    atom.provenance.add("patrol_robot", "agent", "created", {"method": "teleoperation"})
    return atom


def _atom(
    modality: Modality,
    coord: SpatiotemporalCoord,
    text: str,
    tags: list[str],
    fields: dict,
    source_id: str,
    source_type: str,
) -> ExperienceAtom:
    atom = ExperienceAtom(
        modality=modality,
        coord=coord,
        text_summary=text,
        tags=tags,
        structured_fields=fields,
    )
    atom.provenance.add(source_id, source_type, "created")
    return atom
