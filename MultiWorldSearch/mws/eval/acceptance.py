"""Scenario acceptance checks for live/E2E runs (IMPROVEMENT G4).

The mock pytest suite (``tests/scenarios/``) asserts each scenario's acceptance
criteria in-memory. The live ``scenario-all`` ``[2/2]`` leg, however, only
printed metrics — so a real-Gemini + Elasticsearch run was informational, never
gated. This module re-expresses each scenario's headline acceptance criteria
(SCENARIOS.md §3) as pure checks over the produced ``metrics`` dict, so the CLI
can assert them after any run (mock or live) and fail on a miss.

Single source of truth for the *thresholds*: kept deliberately aligned with the
asserts in ``tests/scenarios/test_<scenario>.py``. If those move, move these.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AcceptanceCheck:
    label: str
    passed: bool
    detail: str


def _get(metrics: dict[str, Any], *path: str, default: Any = None) -> Any:
    node: Any = metrics
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


# Each entry: scenario name -> list of (label, predicate(metrics) -> bool).
# Predicates must tolerate missing keys (a missing metric is a failed check).
_CRITERIA: dict[str, list[tuple[str, Callable[[dict[str, Any]], bool]]]] = {
    "maintenance_handoff": [
        ("recall@10 >= 0.5", lambda m: _get(m, "retrieval", "recall_at_10", default=0.0) >= 0.5),
        ("mrr > 0", lambda m: _get(m, "retrieval", "mrr", default=0.0) > 0.0),
        ("skill_transfer_success", lambda m: _get(m, "task", "skill_transfer_success") is True),
        ("all 6 indices fused", lambda m: _get(m, "ablation", "all_indices", "n_indices") == 6.0),
    ],
    "collective_weak_signal": [
        ("lot_L discovered", lambda m: _get(m, "task", "lot_l_discovered") is True),
        ("discovery_margin > 0", lambda m: _get(m, "task", "discovery_margin", default=0) > 0),
        ("lot_L recall > 0", lambda m: _get(m, "task", "lot_l_recall", default=0.0) > 0.0),
        (
            "standing_query_registered",
            lambda m: _get(m, "task", "standing_query_registered") is True,
        ),
    ],
    "incident_response": [
        ("standing_query_fired", lambda m: _get(m, "task", "standing_query_fired") is True),
        ("recon_dispatched", lambda m: _get(m, "task", "recon_dispatched") is True),
        ("plan_uses_sds", lambda m: _get(m, "task", "plan_uses_sds") is True),
        ("plan_uses_exit", lambda m: _get(m, "task", "plan_uses_exit") is True),
        ("plan_uses_roster", lambda m: _get(m, "task", "plan_uses_roster") is True),
    ],
    "new_sku_rampup": [
        ("transfer_gain > 0", lambda m: _get(m, "transfer", "transfer_gain", default=0.0) > 0.0),
        (
            "with-demo success > without",
            lambda m: (
                _get(m, "transfer", "with_demo_success_rate", default=0.0)
                > _get(m, "transfer", "without_demo_success_rate", default=1.0)
            ),
        ),
    ],
    "counterfactual_safety": [
        ("collapse_avoidance", lambda m: _get(m, "task", "collapse_avoidance") is True),
        ("all_hazards_addressed", lambda m: _get(m, "task", "all_hazards_addressed") is True),
    ],
    "order_to_fulfillment": [
        (
            "e2e_success_rate == 1.0",
            lambda m: _get(m, "task", "e2e_success_rate", default=0.0) == 1.0,
        ),
        ("order_success", lambda m: _get(m, "task", "order_success") is True),
    ],
    "physical_record_reconciliation": [
        ("fusion_beats_single", lambda m: _get(m, "fusion", "fusion_beats_single") is True),
        (
            "27 <= fusion_estimate <= 33",
            lambda m: 27.0 <= (_get(m, "fusion", "fusion_estimate", default=0.0) or 0.0) <= 33.0,
        ),
    ],
}


def check_acceptance(scenario_name: str, metrics: dict[str, Any]) -> list[AcceptanceCheck]:
    """Evaluate a scenario's acceptance criteria against its metrics.

    Returns one :class:`AcceptanceCheck` per criterion. An unknown scenario
    returns an empty list (no criteria registered → nothing to gate).
    """
    out: list[AcceptanceCheck] = []
    for label, predicate in _CRITERIA.get(scenario_name, []):
        try:
            ok = bool(predicate(metrics))
        except Exception as exc:  # a malformed metrics dict is a failed check
            out.append(AcceptanceCheck(label, False, f"error: {exc}"))
            continue
        out.append(AcceptanceCheck(label, ok, "ok" if ok else "FAILED"))
    return out


def all_passed(checks: list[AcceptanceCheck]) -> bool:
    return all(c.passed for c in checks)
