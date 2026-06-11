"""Business data for Scenario 7: Order to Fulfillment.

Generates: WMS inventory records (including ghost sku_C), product master,
order record, procurement stub, ERP stub.
"""

from __future__ import annotations

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality


def generate_s7_wms_atoms(seed: int = 0) -> list[ExperienceAtom]:
    """Generate WMS inventory records for sku_A, sku_B, sku_C (ghost)."""
    atoms: list[ExperienceAtom] = []
    base_ts = 1700000000.0

    # sku_A: WMS says 3, physical is 3 (but 1 damaged)
    atoms.append(
        _atom(
            modality=Modality.STRUCTURED_RECORD,
            coord=SpatiotemporalCoord(x=1.0, y=0.0, z=1.0, timestamp=base_ts),
            text="WMS record: sku_A quantity 3 at shelf_A. Product: Widget Alpha.",
            tags=["wms", "inventory", "sku_A", "shelf_A"],
            fields={
                "sku": "sku_A",
                "quantity": 3,
                "location": "shelf_A",
                "source": "wms",
            },
            entity_id="sku_A",
            source_id="wms_system",
            source_type="system",
        )
    )

    # sku_B: WMS says 2, physical is 2
    atoms.append(
        _atom(
            modality=Modality.STRUCTURED_RECORD,
            coord=SpatiotemporalCoord(x=3.0, y=0.0, z=1.0, timestamp=base_ts),
            text="WMS record: sku_B quantity 2 at shelf_B. Product: Widget Beta.",
            tags=["wms", "inventory", "sku_B", "shelf_B"],
            fields={
                "sku": "sku_B",
                "quantity": 2,
                "location": "shelf_B",
                "source": "wms",
            },
            entity_id="sku_B",
            source_id="wms_system",
            source_type="system",
        )
    )

    # sku_C: WMS says 5, physical is 0 (GHOST INVENTORY)
    atoms.append(
        _atom(
            modality=Modality.STRUCTURED_RECORD,
            coord=SpatiotemporalCoord(x=5.0, y=0.0, z=1.0, timestamp=base_ts),
            text="WMS record: sku_C quantity 5 at shelf_C. Product: Widget Gamma.",
            tags=["wms", "inventory", "sku_C", "shelf_C"],
            fields={
                "sku": "sku_C",
                "quantity": 5,
                "location": "shelf_C",
                "source": "wms",
            },
            entity_id="sku_C",
            source_id="wms_system",
            source_type="system",
        )
    )

    return atoms


def generate_s7_product_master(seed: int = 0) -> list[ExperienceAtom]:
    """Generate product master data."""
    atoms: list[ExperienceAtom] = []
    base_ts = 1700000000.0
    default_coord = SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=base_ts)

    for sku, name, weight in [
        ("sku_A", "Widget Alpha", 0.5),
        ("sku_B", "Widget Beta", 0.8),
        ("sku_C", "Widget Gamma", 1.2),
    ]:
        atoms.append(
            _atom(
                modality=Modality.STRUCTURED_RECORD,
                coord=default_coord,
                text=f"Product master: {sku} ({name}), weight {weight}kg.",
                tags=["product_master", sku],
                fields={
                    "sku": sku,
                    "product_name": name,
                    "weight_kg": weight,
                },
                entity_id=sku,
                source_id="product_master",
                source_type="system",
            )
        )

    return atoms


def generate_s7_order_record(seed: int = 0) -> ExperienceAtom:
    """Generate the incoming customer order: sku_A x2, sku_B x1."""
    base_ts = 1700001000.0
    atom = ExperienceAtom(
        modality=Modality.STRUCTURED_RECORD,
        coord=SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=base_ts),
        text_summary="Customer order ORD-501: sku_A qty 2, sku_B qty 1. "
        "Priority: standard. Ship-to: customer warehouse.",
        tags=["order", "sku_A", "sku_B", "fulfillment"],
        entity_id="ORD-501",
        structured_fields={
            "order_id": "ORD-501",
            "sku_A_qty": 2,
            "sku_B_qty": 1,
            "priority": "standard",
        },
    )
    atom.provenance.add("order_system", "system", "created")
    return atom


def _atom(
    modality: Modality,
    coord: SpatiotemporalCoord,
    text: str,
    tags: list[str],
    fields: dict,
    entity_id: str,
    source_id: str,
    source_type: str,
) -> ExperienceAtom:
    atom = ExperienceAtom(
        modality=modality,
        coord=coord,
        text_summary=text,
        tags=tags,
        entity_id=entity_id,
        structured_fields=fields,
    )
    atom.provenance.add(source_id, source_type, "created")
    return atom
