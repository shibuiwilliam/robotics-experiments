"""Acceptance tests for Scenario 5: Real-time Incident Response Coordination.

From SCENARIOS.md:
- Standing query fires on substance leak detection
- Curiosity loop dispatches recon robot for occluded region gap
- IncidentAgent plan uses SDS + exit routes + duty roster
- Deterministic under fixed seed
"""

from mws.core.config import MWSSettings
from mws.core.types import CloudMode
from mws.scenarios.registry import get_scenario


def _run_s5(seed: int = 0, config: dict | None = None) -> dict:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=seed)
    scenario = get_scenario("incident_response")
    return scenario.run(seed=seed, config=config or {}, settings=settings)


def test_s5_completes_mock() -> None:
    """Gate: scenario run completes in mock mode with no keys."""
    result = _run_s5(seed=42)
    assert "run_id" in result
    assert "metrics" in result


def test_s5_standing_query_fires() -> None:
    """Standing query detects substance_X leak and fires."""
    result = _run_s5(seed=42)
    task = result["metrics"]["task"]
    assert task["standing_query_fired"] is True


def test_s5_recon_dispatched_for_gap() -> None:
    """Curiosity loop detects room_B gap and dispatches recon robot."""
    result = _run_s5(seed=42)
    task = result["metrics"]["task"]
    assert task["recon_dispatched"] is True


def test_s5_plan_uses_all_sources() -> None:
    """IncidentAgent plan uses SDS + exit routes + duty roster."""
    result = _run_s5(seed=42)
    task = result["metrics"]["task"]
    assert task["plan_uses_sds"] is True, "Plan must reference SDS"
    assert task["plan_uses_exit"] is True, "Plan must reference exit routes"
    assert task["plan_uses_roster"] is True, "Plan must reference duty roster"


def test_s5_withholding_sds_unmakes_plan_and_step() -> None:
    """Falsifiability: if the SDS atom is not in memory, retrieval can't surface
    it, so the plan does NOT use SDS and the retrieve_sds step is incomplete.
    plan_uses_* and all_steps_complete are computed, not asserted True."""
    result = _run_s5(seed=42, config={"omit_evidence_tags": ["sds"]})
    task = result["metrics"]["task"]
    assert task["plan_uses_sds"] is False
    # Exit and roster evidence are still present → those remain usable.
    assert task["plan_uses_exit"] is True
    assert task["plan_uses_roster"] is True
    assert task["all_steps_complete"] is False


def test_s5_deterministic() -> None:
    """Same seed produces identical task metrics."""
    r1 = _run_s5(seed=99)
    r2 = _run_s5(seed=99)
    assert r1["metrics"]["task"] == r2["metrics"]["task"]
    assert r1["metrics"]["retrieval"] == r2["metrics"]["retrieval"]
