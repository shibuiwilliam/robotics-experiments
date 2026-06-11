"""Business data for Scenario 5: incident response.

Generates: SDS for substance_X, safety SOP, building floor plan (exits),
duty roster (on-call personnel).
"""

from __future__ import annotations

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality


def generate_s5_business_atoms(seed: int = 0) -> list[ExperienceAtom]:
    """Generate scenario 5 business data as atoms."""
    atoms: list[ExperienceAtom] = []
    default_coord = SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=1700000000.0)

    # SDS (Safety Data Sheet) for substance_X
    atoms.append(
        _atom(
            modality=Modality.SDS,
            coord=default_coord,
            text="Safety Data Sheet: substance_X. Classification: toxic, corrosive. "
            "Hazard: inhalation and skin contact. Requires ventilation and PPE level C "
            "(full-face respirator, chemical-resistant suit). "
            "First aid: move to fresh air, flush skin with water. "
            "Spill: contain with absorbent, ventilate area, notify hazmat team.",
            tags=["sds", "substance_X", "safety", "hazmat", "ppe", "toxic"],
            fields={
                "substance_id": "substance_X",
                "classification": "toxic",
                "ppe_level": "C",
                "requires_ventilation": True,
                "doc_type": "sds",
            },
            source_id="safety_document_store",
            source_type="system",
        )
    )

    # Safety SOP
    atoms.append(
        _atom(
            modality=Modality.SOP,
            coord=default_coord,
            text="Safety SOP: Chemical Leak Response.\n"
            "1. Detect and confirm leak (visual or sensor).\n"
            "2. Sound alarm and notify on-call safety officer.\n"
            "3. Evacuate non-essential personnel via nearest exit.\n"
            "4. Don PPE per SDS requirements.\n"
            "5. Contain spill with absorbent materials.\n"
            "6. Ventilate affected area.\n"
            "7. Monitor air quality until safe levels confirmed.\n"
            "8. Debrief and file incident report.",
            tags=["sop", "safety", "leak", "chemical", "evacuation", "incident"],
            fields={
                "sop_id": "SOP-SAFETY-001",
                "category": "chemical_leak",
                "doc_type": "sop",
            },
            source_id="safety_document_store",
            source_type="system",
        )
    )

    # Building floor plan with exit locations
    atoms.append(
        _atom(
            modality=Modality.DOCUMENT,
            coord=default_coord,
            text="Building Floor Plan: Incident Facility.\n"
            "room_A: main processing area (x=0..4, y=-3..3). Contains chemical storage.\n"
            "room_B: storage annex (x=5..9, y=-3..3). Behind partition, limited sensor coverage.\n"
            "exit_1: main exit, south side (x=0, y=-5). Leads to assembly point A.\n"
            "exit_2: secondary exit, east side (x=8, y=0). Leads to assembly point B.\n"
            "Evacuation route: room_A → exit_1 (primary), room_B → exit_2 (primary).",
            tags=["floor_plan", "exit", "evacuation", "building", "room_A", "room_B"],
            fields={
                "doc_type": "floor_plan",
                "exit_1_pos": "0,-5",
                "exit_2_pos": "8,0",
                "facility": "incident_facility",
            },
            source_id="facilities_system",
            source_type="system",
        )
    )

    # Duty roster (on-call personnel)
    atoms.append(
        _atom(
            modality=Modality.STRUCTURED_RECORD,
            coord=default_coord,
            text="Duty Roster: Safety Officer T-Suzuki on call 08:00-20:00. "
            "Backup: T-Nakamura on call 20:00-08:00. "
            "T-Suzuki certified for hazmat response, substance_X handling. "
            "Contact: radio channel 5, phone ext 2200.",
            tags=["duty_roster", "personnel", "safety", "on_call", "hazmat"],
            fields={
                "roster_id": "ROSTER-SAFETY-001",
                "primary": "T-Suzuki",
                "backup": "T-Nakamura",
                "doc_type": "duty_roster",
            },
            source_id="hr_system",
            source_type="system",
        )
    )

    return atoms


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
