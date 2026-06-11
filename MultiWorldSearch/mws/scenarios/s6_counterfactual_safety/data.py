"""Business data for Scenario 6: counterfactual safety.

Generates: SOP with risk limits, equipment specs with safe operating parameters.
"""

from __future__ import annotations

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality


def generate_s6_sop_atoms(
    seed: int = 0,
    max_stack_height: int = 2,
    max_pressure_psi: int = 100,
) -> list[ExperienceAtom]:
    """Generate SOP and equipment spec atoms with safety limits.

    The limits are parameters (not hard-coded into the decision): the scenario
    computes risk by comparing observed hazards against these retrieved limits,
    so loosening a limit here changes the downstream safety verdict.
    """
    atoms: list[ExperienceAtom] = []
    default_coord = SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=1700000000.0)

    # SOP: cargo stacking risk limits
    atoms.append(
        _atom(
            modality=Modality.SOP,
            coord=default_coord,
            text=(
                "SOP-CARGO-STACK: Cargo stacking safety procedure.\n"
                f"1. Maximum stack height: {max_stack_height} boxes.\n"
                f"2. Stacks exceeding {max_stack_height} boxes must be reduced before "
                "any manipulation.\n"
                "3. Never move a box from a stack exceeding the limit without first "
                "securing the remaining boxes.\n"
                f"4. Report any stack exceeding {max_stack_height} boxes as a hazard."
            ),
            tags=["sop", "cargo", "stacking", "safety", "risk_limit"],
            fields={
                "sop_id": "SOP-CARGO-STACK",
                "max_stack_height": max_stack_height,
                "hazard_type": "topple_risk",
            },
            source_id="safety_document_store",
            source_type="system",
        )
    )

    # SOP: valve pressure limits
    atoms.append(
        _atom(
            modality=Modality.SOP,
            coord=default_coord,
            text=(
                "SOP-VALVE-PRESSURE: Pressurized valve safety procedure.\n"
                f"1. Maximum safe operating pressure: {max_pressure_psi} PSI.\n"
                "2. Before any valve operation, verify pressure is within limits.\n"
                f"3. If pressure exceeds {max_pressure_psi} PSI, initiate controlled "
                "pressure reduction before performing any maintenance.\n"
                "4. Never open a valve with pressure exceeding the safe limit."
            ),
            tags=["sop", "valve", "pressure", "safety", "risk_limit"],
            fields={
                "sop_id": "SOP-VALVE-PRESSURE",
                "max_pressure_psi": max_pressure_psi,
                "hazard_type": "overpressure",
            },
            source_id="safety_document_store",
            source_type="system",
        )
    )

    # Equipment spec: cargo boxes
    atoms.append(
        _atom(
            modality=Modality.STRUCTURED_RECORD,
            coord=SpatiotemporalCoord(x=1.0, y=1.0, z=0.25, timestamp=1700000000.0),
            text=(
                "Equipment spec: Standard cargo box. Dimensions 0.5m x 0.5m x 0.5m, "
                f"mass 5 kg. Safe stacking limit: {max_stack_height} units. "
                "Exceeding limit increases topple risk to unacceptable levels."
            ),
            tags=["equipment", "cargo", "spec", "safety"],
            fields={
                "equipment_type": "cargo_box",
                "mass_kg": 5.0,
                "safe_stack_limit": max_stack_height,
            },
            source_id="equipment_registry",
            source_type="system",
        )
    )

    # Equipment spec: pressurized valve
    atoms.append(
        _atom(
            modality=Modality.STRUCTURED_RECORD,
            coord=SpatiotemporalCoord(x=3.0, y=2.0, z=0.4, timestamp=1700000000.0),
            text=(
                "Equipment spec: Pressurized valve PV-01. "
                f"Max safe operating pressure: {max_pressure_psi} PSI. "
                "Burst pressure: 200 PSI. "
                f"If pressure exceeds {max_pressure_psi} PSI, initiate emergency bleed-down."
            ),
            tags=["equipment", "valve", "pressure", "spec", "safety"],
            fields={
                "equipment_type": "pressurized_valve",
                "equipment_id": "PV-01",
                "max_pressure_psi": max_pressure_psi,
                "burst_pressure_psi": 200,
            },
            source_id="equipment_registry",
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
