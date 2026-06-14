"""Cross-scenario coverage for cloud-cost stamping (G1) and perception tax (G3).

Runs each scenario once in mock + in-memory (offline, deterministic) and asserts:
- G1: cloud usage + estimated cost recorded in BOTH metrics and manifest.
- G3: every scenario records ``perception_tax`` — computed (oracle↔pipeline gap)
  for retrieval-bearing scenarios, or an explicit ``{applicable: false, reason}``
  for task-success scenarios (no silent omission).
"""

from __future__ import annotations

import json

import pytest

from mws.core.config import MWSSettings, get_settings
from mws.core.types import CloudMode
from mws.scenarios.registry import get_scenario

RETRIEVAL_SCENARIOS = [
    "maintenance_handoff",
    "collective_weak_signal",
    "incident_response",
    "order_to_fulfillment",
]
TASK_SCENARIOS = [
    "physical_record_reconciliation",
    "new_sku_rampup",
    "counterfactual_safety",
]
ALL_SCENARIOS = RETRIEVAL_SCENARIOS + TASK_SCENARIOS


def _run(name: str) -> dict:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=0)
    return get_scenario(name).run(seed=0, config={}, settings=settings)


@pytest.mark.parametrize("name", ALL_SCENARIOS)
def test_cloud_cost_recorded_in_metrics_and_manifest(name: str) -> None:
    result = _run(name)
    metrics = result["metrics"]
    # G1: metrics carry the cloud usage summary with an estimated cost.
    assert "cloud" in metrics, f"{name}: metrics missing 'cloud'"
    cloud = metrics["cloud"]
    for key in ("estimated_cost_usd", "llm_calls_real", "embedding_requests"):
        assert key in cloud, f"{name}: cloud summary missing {key}"
    # G1: the manifest also carries it (the cost/reproducibility ledger).
    run_dir = get_settings().run_dir / result["run_id"]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert "cloud" in manifest, f"{name}: manifest missing 'cloud'"
    # Mock runs make no REAL cloud calls.
    assert cloud["llm_calls_real"] == 0


@pytest.mark.parametrize("name", ALL_SCENARIOS)
def test_perception_tax_present(name: str) -> None:
    metrics = _run(name)["metrics"]
    assert "perception_tax" in metrics, f"{name}: missing perception_tax (G3)"


@pytest.mark.parametrize("name", RETRIEVAL_SCENARIOS)
def test_retrieval_scenarios_compute_tax(name: str) -> None:
    pt = _run(name)["metrics"]["perception_tax"]
    # Computed form has oracle + tax sub-dicts (mws.eval.oracle.perception_tax).
    assert "tax" in pt and "oracle" in pt, f"{name}: tax not computed"
    assert "recall_at_10" in pt["tax"]


@pytest.mark.parametrize("name", TASK_SCENARIOS)
def test_task_scenarios_mark_tax_not_applicable(name: str) -> None:
    pt = _run(name)["metrics"]["perception_tax"]
    assert pt.get("applicable") is False, f"{name}: should mark tax N/A"
    assert pt.get("reason"), f"{name}: must give a reason for N/A tax"
