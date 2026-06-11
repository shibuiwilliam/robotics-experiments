"""Business data for Scenario 3: collective weak signal discovery.

Generates: MES lot records mapping objects to lots, supplier registry.
Lot L (lot_L) contains obj_01, obj_04, obj_07, obj_10 — all with micro_defect.
"""

from __future__ import annotations

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality

# Ground truth: which objects belong to lot_L and carry micro_defect
LOT_L_OBJECTS = {"obj_01", "obj_04", "obj_07", "obj_10"}
ALL_OBJECTS = [f"obj_{i:02d}" for i in range(1, 11)]

# Lot assignments — lot_L gets the 4 defective objects, others get lot_M/lot_N
LOT_ASSIGNMENTS: dict[str, str] = {}
for _obj in ALL_OBJECTS:
    if _obj in LOT_L_OBJECTS:
        LOT_ASSIGNMENTS[_obj] = "lot_L"
    elif _obj in {"obj_02", "obj_05", "obj_08"}:
        LOT_ASSIGNMENTS[_obj] = "lot_M"
    else:
        LOT_ASSIGNMENTS[_obj] = "lot_N"


def generate_s3_business_atoms(seed: int = 0) -> list[ExperienceAtom]:
    """Generate MES lot records and supplier registry as atoms."""
    atoms: list[ExperienceAtom] = []
    base_ts = 1700000000.0

    # MES lot records — one atom per object
    for obj_id in ALL_OBJECTS:
        lot = LOT_ASSIGNMENTS[obj_id]
        has_defect = obj_id in LOT_L_OBJECTS
        idx = int(obj_id.split("_")[1])
        coord = SpatiotemporalCoord(x=float(idx - 1), y=0.0, z=0.15, timestamp=base_ts)
        atom = ExperienceAtom(
            modality=Modality.STRUCTURED_RECORD,
            coord=coord,
            text_summary=(
                f"MES lot record: {obj_id} belongs to {lot}. "
                f"Supplier: {'supplier_A' if lot == 'lot_L' else 'supplier_B'}. "
                f"Micro-defect: {'yes' if has_defect else 'no'}."
            ),
            entity_id=obj_id,
            tags=["mes", "lot_record", lot, obj_id],
            structured_fields={
                "object_id": obj_id,
                "lot": lot,
                "supplier": "supplier_A" if lot == "lot_L" else "supplier_B",
                "micro_defect": has_defect,
            },
        )
        atom.provenance.add("mes_system", "system", "created")
        atoms.append(atom)

    # Supplier registry
    for supplier_id, lots in [
        ("supplier_A", ["lot_L"]),
        ("supplier_B", ["lot_M", "lot_N"]),
    ]:
        atom = ExperienceAtom(
            modality=Modality.STRUCTURED_RECORD,
            coord=SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=base_ts),
            text_summary=(
                f"Supplier registry: {supplier_id} supplies lots {', '.join(lots)}. "
                f"Quality rating: {'B' if supplier_id == 'supplier_A' else 'A'}."
            ),
            tags=["supplier", "registry", supplier_id, *lots],
            structured_fields={
                "supplier_id": supplier_id,
                "lots": ",".join(lots),
                "quality_rating": "B" if supplier_id == "supplier_A" else "A",
            },
        )
        atom.provenance.add("supplier_registry", "system", "created")
        atoms.append(atom)

    return atoms
