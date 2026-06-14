"""Tests for the scenario acceptance checks (IMPROVEMENT G4)."""

from __future__ import annotations

from mws.eval.acceptance import all_passed, check_acceptance


def test_passing_metrics_all_pass() -> None:
    metrics = {
        "retrieval": {"recall_at_10": 0.9, "mrr": 1.0},
        "task": {"skill_transfer_success": True},
        "ablation": {"all_indices": {"n_indices": 6.0}},
    }
    checks = check_acceptance("maintenance_handoff", metrics)
    assert len(checks) == 4
    assert all_passed(checks)


def test_failing_metric_is_caught() -> None:
    metrics = {
        "retrieval": {"recall_at_10": 0.1, "mrr": 1.0},  # recall too low
        "task": {"skill_transfer_success": True},
        "ablation": {"all_indices": {"n_indices": 6.0}},
    }
    checks = check_acceptance("maintenance_handoff", metrics)
    assert not all_passed(checks)
    failed = [c for c in checks if not c.passed]
    assert any("recall@10" in c.label for c in failed)


def test_missing_metric_is_a_failure_not_crash() -> None:
    checks = check_acceptance("maintenance_handoff", {})
    assert checks  # criteria exist
    assert not all_passed(checks)  # all fail gracefully (no KeyError)


def test_unknown_scenario_returns_no_criteria() -> None:
    assert check_acceptance("does_not_exist", {"task": {}}) == []


def test_all_seven_scenarios_have_criteria() -> None:
    names = [
        "maintenance_handoff",
        "physical_record_reconciliation",
        "collective_weak_signal",
        "new_sku_rampup",
        "incident_response",
        "counterfactual_safety",
        "order_to_fulfillment",
    ]
    for n in names:
        assert check_acceptance(n, {}), f"{n} has no acceptance criteria"


def test_order_to_fulfillment_threshold() -> None:
    good = {"task": {"e2e_success_rate": 1.0, "order_success": True}}
    bad = {"task": {"e2e_success_rate": 0.8, "order_success": True}}
    assert all_passed(check_acceptance("order_to_fulfillment", good))
    assert not all_passed(check_acceptance("order_to_fulfillment", bad))
