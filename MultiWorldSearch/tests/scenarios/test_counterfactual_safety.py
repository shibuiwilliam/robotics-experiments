"""Acceptance tests for Scenario 6: Counterfactual Simulation for Safety Decisions.

From SCENARIOS.md:
- Rollout predicts collapse -> avoids unsafe action
- Counterfactual result atoms stored and retrievable
- Deterministic under fixed seed
"""

from mws.core.config import MWSSettings
from mws.core.types import CloudMode
from mws.scenarios.registry import get_scenario
from mws.scenarios.safety_rules import assess_risk, exceedance_risk


def _run_s6(seed: int = 0, config: dict | None = None) -> dict:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=seed)
    scenario = get_scenario("counterfactual_safety")
    return scenario.run(seed=seed, config=config or {}, settings=settings)


def test_s6_completes_mock() -> None:
    """Gate: scenario run completes in mock mode with no keys."""
    result = _run_s6(seed=42)
    assert "run_id" in result
    assert "metrics" in result
    assert "task" in result["metrics"]


def test_s6_unsafe_action_avoided() -> None:
    """Collapse avoidance: unsafe cargo action was avoided after counterfactual rollout."""
    result = _run_s6(seed=42)
    task = result["metrics"]["task"]
    assert task["collapse_avoidance"] is True
    assert task["all_hazards_addressed"] is True
    # Both decisions should be AVOID
    for decision in task["decisions"]:
        assert decision["decision"] == "AVOID"


def test_s6_counterfactual_atoms_stored_and_retrievable() -> None:
    """Counterfactual result atoms are stored in MWS and can be retrieved."""
    result = _run_s6(seed=42)
    task = result["metrics"]["task"]
    assert task["counterfactual_atoms_stored"] == 2
    assert task["counterfactual_atoms_retrievable"] >= 1


def test_s6_risk_scores_are_computed_not_constant() -> None:
    """Risk scores come from the rule engine (limit exceedance), so changing
    the risk sensitivity changes them — they are not the literals 0.92/0.87."""
    base = _run_s6(seed=42)
    sharper = _run_s6(seed=42, config={"risk_sensitivity": 20.0})
    base_scores = [d["risk_score"] for d in base["metrics"]["task"]["decisions"]]
    sharp_scores = [d["risk_score"] for d in sharper["metrics"]["task"]["decisions"]]
    assert base_scores != sharp_scores, "risk scores must depend on sensitivity"
    # And none of them is the old hard-coded constant.
    assert 0.92 not in base_scores and 0.87 not in base_scores


def test_s6_loosened_sop_flips_decision_to_proceed() -> None:
    """Falsifiability: if the SOP stack limit is raised above the observed
    stack, the computed risk drops below threshold and the agent PROCEEDs —
    collapse avoidance must then be False."""
    result = _run_s6(seed=42, config={"max_stack_height": 5, "max_pressure_psi": 200})
    task = result["metrics"]["task"]
    assert task["collapse_avoidance"] is False
    for decision in task["decisions"]:
        assert decision["decision"] == "PROCEED"


def test_s6_deterministic() -> None:
    """Same seed produces identical task metrics."""
    r1 = _run_s6(seed=99)
    r2 = _run_s6(seed=99)
    assert r1["metrics"]["task"] == r2["metrics"]["task"]


# --- Pure rule-engine unit tests ---


def test_exceedance_risk_monotonic_and_zero_below_limit() -> None:
    assert exceedance_risk(2, 2, 8.0) == 0.0  # at limit → no risk
    assert exceedance_risk(1, 2, 8.0) == 0.0  # below limit → no risk
    r_small = exceedance_risk(3, 2, 8.0)
    r_big = exceedance_risk(6, 2, 8.0)
    assert 0.0 < r_small < r_big < 1.0  # rises with exceedance, saturates < 1


def test_assess_risk_decision_threshold() -> None:
    avoid = assess_risk(3, 2, sensitivity=8.0, threshold=0.5)
    assert avoid.decision == "AVOID" and avoid.exceeds_limit is True
    proceed = assess_risk(3, 5, sensitivity=8.0, threshold=0.5)
    assert proceed.decision == "PROCEED" and proceed.exceeds_limit is False
    assert proceed.risk_score == 0.0
