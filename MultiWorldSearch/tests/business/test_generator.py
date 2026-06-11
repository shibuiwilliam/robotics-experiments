"""Tests for business data generation."""

from mws.business.generator import business_data_to_atoms, generate_maintenance_data


def test_generate_data() -> None:
    data = generate_maintenance_data(seed=0)
    assert len(data["work_orders"]) > 0
    assert len(data["inventory"]) > 0
    assert len(data["sops"]) > 0
    assert len(data["maintenance_logs"]) > 0


def test_data_to_atoms() -> None:
    data = generate_maintenance_data(seed=0)
    atoms = business_data_to_atoms(data, seed=0)
    assert len(atoms) > 0
    # Check we have different modalities
    modalities = {a.modality for a in atoms}
    assert len(modalities) >= 2


def test_deterministic() -> None:
    d1 = generate_maintenance_data(seed=42)
    d2 = generate_maintenance_data(seed=42)
    assert d1["work_orders"][0].work_order_id == d2["work_orders"][0].work_order_id
