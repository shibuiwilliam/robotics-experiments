"""Business data generator for Scenario 2: physical-record reconciliation.

Generates WMS records, asset registry entries, and observer configurations
with configurable parameters for drift, noise, and mismatch counts.
"""

from __future__ import annotations

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality


def generate_s2_wms_records(
    n_shelves: int = 3,
    drift_magnitude: int = 20,
    seed: int = 0,
) -> list[ExperienceAtom]:
    """Generate WMS inventory records with configurable drift.

    Each shelf gets a WMS record. The first shelf has drift (recorded count
    inflated by drift_magnitude above ground truth).
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    atoms: list[ExperienceAtom] = []

    ground_truths = [30, 45, 12]  # actual counts per shelf
    sku_names = ["SKU-WIDGET-A", "SKU-BOLT-B", "SKU-GASKET-C"]
    shelf_names = ["shelf_A", "shelf_B", "shelf_C"]

    for i in range(min(n_shelves, len(ground_truths))):
        gt = ground_truths[i]
        recorded = gt + drift_magnitude if i == 0 else gt + int(rng.integers(-2, 3))
        coord = SpatiotemporalCoord(x=2.0 + i * 2, y=0.0, z=1.0, timestamp=1699900000.0)
        atom = ExperienceAtom(
            modality=Modality.STRUCTURED_RECORD,
            coord=coord,
            text_summary=(
                f"WMS Record: {sku_names[i]} at {shelf_names[i]}, "
                f"recorded qty={recorded}, last updated 48h ago"
            ),
            tags=["wms", "inventory", shelf_names[i], sku_names[i]],
            structured_fields={
                "entity_id": shelf_names[i],
                "sku": sku_names[i],
                "recorded_count": recorded,
                "ground_truth_count": gt,
                "location": shelf_names[i],
            },
        )
        atom.provenance.add("wms_system", "system", "created")
        atoms.append(atom)

    return atoms


def generate_s2_asset_records(seed: int = 0) -> list[ExperienceAtom]:
    """Generate asset registry records with status mismatches."""
    atoms: list[ExperienceAtom] = []

    # Forklift: registry says "in_maintenance" but it's on the floor
    coord = SpatiotemporalCoord(x=3.0, y=3.0, z=0.3, timestamp=1699900000.0)
    atom = ExperienceAtom(
        modality=Modality.STRUCTURED_RECORD,
        coord=coord,
        text_summary=(
            "Asset Registry: forklift_12 status=in_maintenance, "
            "expected location=maintenance_bay. Last audit 7 days ago."
        ),
        tags=["asset_registry", "forklift_12", "maintenance"],
        structured_fields={
            "asset_id": "forklift_12",
            "entity_id": "forklift_12",
            "status": "in_maintenance",
            "expected_location": "maintenance_bay",
        },
    )
    atom.provenance.add("asset_registry", "system", "created")
    atoms.append(atom)

    # Crane: normal status (no mismatch)
    coord2 = SpatiotemporalCoord(x=8.0, y=0.0, z=2.0, timestamp=1699900000.0)
    atom2 = ExperienceAtom(
        modality=Modality.STRUCTURED_RECORD,
        coord=coord2,
        text_summary="Asset Registry: crane_01 status=in_service, location=bay_A.",
        tags=["asset_registry", "crane_01"],
        structured_fields={
            "asset_id": "crane_01",
            "entity_id": "crane_01",
            "status": "in_service",
            "expected_location": "bay_A",
        },
    )
    atom2.provenance.add("asset_registry", "system", "created")
    atoms.append(atom2)

    return atoms


def generate_s2_observer_atoms(
    ground_truth: int = 30,
    n_observers: int = 2,
    noise_sigmas: list[float] | None = None,
    trusts: list[float] | None = None,
    seed: int = 0,
) -> list[ExperienceAtom]:
    """Generate multi-observer inventory count observations.

    Each observer sees the ground truth count with Gaussian noise.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    if noise_sigmas is None:
        noise_sigmas = [0.5, 2.0]
    if trusts is None:
        trusts = [0.8, 0.6]

    atoms: list[ExperienceAtom] = []
    robot_names = [f"robot_{chr(65 + i)}" for i in range(n_observers)]

    for i in range(n_observers):
        noise = rng.normal(0, noise_sigmas[i % len(noise_sigmas)])
        observed = round(ground_truth + noise)
        trust = trusts[i % len(trusts)]

        coord = SpatiotemporalCoord(x=2.0, y=float(i * 2 - 1), z=0.5, timestamp=1700001000.0)
        atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=coord,
            trust=trust,
            text_summary=(
                f"Inventory observation by {robot_names[i]}: "
                f"shelf_A count={observed} (noise_sigma={noise_sigmas[i % len(noise_sigmas)]})"
            ),
            entity_id="shelf_A",
            tags=["observation", "inventory", "shelf_A", robot_names[i]],
            structured_fields={
                "entity_id": "shelf_A",
                "observed_count": observed,
                "observer": robot_names[i],
                "trust": trust,
            },
        )
        atom.provenance.add(f"{robot_names[i]}:scanner", "sensor", "created")
        atoms.append(atom)

    return atoms
