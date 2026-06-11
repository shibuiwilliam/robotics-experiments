"""Acceptance tests for Scenario 7: Order to Fulfillment End-to-End.

From SCENARIOS.md:
- Ghost inventory detected (physical observation overrides WMS)
- WMS write-back corrects ghost
- Procurement re-order triggered
- Customer notification sent
- ERP order closed
- Order fulfilled despite damaged sku_A
- Deterministic under fixed seed
"""

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.config import MWSSettings
from mws.core.types import CloudMode, Modality
from mws.scenarios.freshness import reconcile_inventory
from mws.scenarios.registry import get_scenario


def _run_s7(seed: int = 0, config: dict | None = None) -> dict:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=seed)
    scenario = get_scenario("order_to_fulfillment")
    return scenario.run(seed=seed, config=config or {}, settings=settings)


def _wms_atom(qty: int, ts: float) -> ExperienceAtom:
    return ExperienceAtom(
        modality=Modality.STRUCTURED_RECORD,
        coord=SpatiotemporalCoord(x=5.0, y=0.0, z=1.0, timestamp=ts),
        entity_id="sku_C",
        structured_fields={"sku": "sku_C", "quantity": qty, "source": "wms"},
    )


def _phys_atom(qty: int, ts: float) -> ExperienceAtom:
    return ExperienceAtom(
        modality=Modality.TELEMETRY,
        coord=SpatiotemporalCoord(x=5.0, y=0.0, z=1.0, timestamp=ts),
        entity_id="sku_C",
        structured_fields={
            "sku": "sku_C",
            "quantity": qty,
            "source": "physical",
            "observed_at": ts,
        },
    )


def test_s7_completes_mock() -> None:
    """Gate: scenario run completes in mock mode with no keys."""
    result = _run_s7(seed=42)
    assert "run_id" in result
    assert "metrics" in result
    assert result["metrics"]["task"]["e2e_success_rate"] == 1.0


def test_s7_ghost_inventory_detected() -> None:
    """Physical observation overrides WMS ghost inventory for sku_C."""
    result = _run_s7(seed=42)
    task = result["metrics"]["task"]
    assert task["ghost_inventory_detected"] is True


def test_s7_wms_write_back() -> None:
    """WMS corrected from qty=5 to qty=0 for sku_C after ghost detection."""
    result = _run_s7(seed=42)
    task = result["metrics"]["task"]
    assert task["wms_write_back"] is True


def test_s7_procurement_and_notification() -> None:
    """Procurement re-order triggered and customer notified."""
    result = _run_s7(seed=42)
    task = result["metrics"]["task"]
    assert task["procurement_triggered"] is True
    assert task["customer_notified"] is True
    assert task["erp_closed"] is True
    assert task["order_success"] is True


def test_s7_deterministic() -> None:
    """Same seed produces identical task metrics."""
    r1 = _run_s7(seed=99)
    r2 = _run_s7(seed=99)
    assert r1["metrics"]["task"] == r2["metrics"]["task"]
    assert r1["metrics"]["retrieval"] == r2["metrics"]["retrieval"]


# --- Falsifiability: the override must be GATED on freshness (H4) ---


def test_s7_stale_physical_does_not_override() -> None:
    """If the physical observation is OLDER than the WMS record, the stale-record
    override must NOT fire: no ghost detected, no write-back, e2e < 1.0."""
    result = _run_s7(seed=42, config={"physical_obs_fresh": False})
    task = result["metrics"]["task"]
    assert task["ghost_inventory_detected"] is False
    assert task["wms_write_back"] is False
    assert task["procurement_triggered"] is False
    assert task["e2e_success_rate"] < 1.0


def test_s7_matching_counts_no_ghost() -> None:
    """If the fresh physical count matches WMS (5==5), there is no discrepancy
    and therefore no ghost detection."""
    result = _run_s7(seed=42, config={"physical_quantity_sku_c": 5})
    task = result["metrics"]["task"]
    assert task["ghost_inventory_detected"] is False
    assert task["wms_write_back"] is False


def test_reconcile_helper_freshness_gating() -> None:
    """Unit test the pure reconciliation rule directly."""
    # Fresh physical (ts later) with fewer units → ghost detected.
    fresh = reconcile_inventory([_wms_atom(5, 100.0), _phys_atom(0, 200.0)])
    assert fresh.ghost_detected is True
    assert fresh.override_quantity == 0

    # Stale physical (ts earlier) → must NOT override even if counts differ.
    stale = reconcile_inventory([_wms_atom(5, 200.0), _phys_atom(0, 100.0)])
    assert stale.ghost_detected is False
    assert stale.override_quantity == 5  # keep the (newer) record

    # Agreeing counts → no ghost regardless of freshness.
    agree = reconcile_inventory([_wms_atom(5, 100.0), _phys_atom(5, 200.0)])
    assert agree.ghost_detected is False
