"""Acceptance tests for Scenario 4: New SKU Rampup via Swarm Transfer.

From SCENARIOS.md:
- H1: shared memory improves unexperienced instances
- Transfer gain: with-demo success rate > without-demo success rate
- Deterministic under fixed seed
"""

from mws.core.config import MWSSettings
from mws.core.types import CloudMode
from mws.scenarios.registry import get_scenario
from mws.scenarios.s4_new_sku_rampup.scenario import NewSkuRampupScenario


def _run_s4(seed: int = 0, with_demo: bool = True, **extra: object) -> dict:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=seed)
    scenario = get_scenario("new_sku_rampup")
    config: dict = {"with_demo": with_demo, **extra}
    return scenario.run(seed=seed, config=config, settings=settings)


def test_s4_completes_mock() -> None:
    """Gate: scenario run completes in mock mode with no keys."""
    result = _run_s4(seed=42, with_demo=True)
    assert "run_id" in result
    assert "metrics" in result
    assert result["metrics"]["task"]["with_demo"] is True
    assert result["metrics"]["task"]["n_swarm_members"] > 0

    # Also verify without-demo completes
    result_no = _run_s4(seed=42, with_demo=False)
    assert "run_id" in result_no
    assert result_no["metrics"]["task"]["with_demo"] is False


def test_s4_transfer_gain_positive() -> None:
    """H1: with-demo success rate > without-demo success rate."""
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=42)
    ab = NewSkuRampupScenario.run_ab_experiment(seed=42, settings=settings)

    assert ab["success_rate_with"] > ab["success_rate_without"]
    assert ab["transfer_gain"] > 0
    assert ab["h1_confirmed"] is True

    # With demo: all swarm members should succeed (confidence 0.9)
    assert ab["success_rate_with"] == 1.0
    # Without demo: no swarm members should succeed (confidence 0.3)
    assert ab["success_rate_without"] == 0.0


def test_s4_retrieval_is_necessary_but_not_sufficient() -> None:
    """Falsifiability: tighten the grip-force limit below the demo's calibrated
    force. The skill demo is STILL retrieved, but executing it now exceeds the
    fragile-item limit, so the pick FAILS. Success is gated on execution, not
    merely on retrieving a demo."""
    result = _run_s4(seed=42, with_demo=True, force_limit_n=3.0)
    task = result["metrics"]["task"]
    # The demo was retrieved by every member...
    assert all(r["has_skill_demo"] for r in task["swarm_results"])
    # ...yet execution exceeds the 3.0 N limit (demo executes ~4.0 N), so it fails.
    assert all(r["within_force_limit"] is False for r in task["swarm_results"])
    assert task["success_rate"] == 0.0


def test_s4_transfer_gain_vanishes_when_demo_unusable() -> None:
    """When the constraint makes the demo unusable, transfer gain disappears —
    H1 is not confirmed. The metric reflects reality, not a hard-coded win."""
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=42)
    scenario = get_scenario("new_sku_rampup")
    res = scenario.run(seed=42, config={"with_demo": True, "force_limit_n": 3.0}, settings=settings)
    transfer = res["metrics"]["transfer"]
    assert transfer["transfer_gain"] <= 0.0


def test_s4_deterministic() -> None:
    """Same seed produces identical metrics."""
    r1 = _run_s4(seed=99, with_demo=True)
    r2 = _run_s4(seed=99, with_demo=True)
    assert r1["metrics"]["task"]["success_rate"] == r2["metrics"]["task"]["success_rate"]
    assert r1["metrics"]["task"]["avg_confidence"] == r2["metrics"]["task"]["avg_confidence"]

    r3 = _run_s4(seed=99, with_demo=False)
    r4 = _run_s4(seed=99, with_demo=False)
    assert r3["metrics"]["task"]["success_rate"] == r4["metrics"]["task"]["success_rate"]
