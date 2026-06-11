"""Acceptance tests for Scenario 3: Collective Weak Signal Discovery.

From SCENARIOS.md:
- Consolidation surfaces lot_L pattern from scattered observations
- Standing query registered for ongoing lot_L monitoring
- Cluster purity: all defect atoms belong to lot_L
- Deterministic under fixed seed
"""

from mws.core.config import MWSSettings
from mws.core.types import CloudMode
from mws.scenarios.registry import get_scenario


def _run_s3(seed: int = 0, config: dict | None = None) -> dict:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=seed)
    scenario = get_scenario("collective_weak_signal")
    return scenario.run(seed=seed, config=config or {}, settings=settings)


def test_s3_completes_mock() -> None:
    """Gate: scenario run completes in mock mode with no keys."""
    result = _run_s3(seed=42)
    assert "run_id" in result
    assert "metrics" in result
    assert result["metrics"]["system"]["total_atoms"] > 0


def test_s3_lot_pattern_discovered_above_noise() -> None:
    """Consolidation surfaces lot_L as the dominant defect pattern even with
    competing-lot noise present, and signal purity reflects the noise."""
    result = _run_s3(seed=42)
    task = result["metrics"]["task"]
    assert task["lot_l_discovered"] is True, "lot_L pattern should be discovered"
    assert task["discovery_margin"] > 0, "lot_L must strictly dominate the runner-up"
    # With default noise (2), purity is below 1.0 — not a structural constant.
    assert task["n_noise_defects"] == 2
    assert task["signal_purity"] < 1.0
    assert task["signal_purity"] == 3 / (3 + 2)
    assert task["lot_l_recall"] > 0.0, "At least some lot_L defects should be retrieved"


def test_s3_zero_noise_purity_is_one() -> None:
    """With noise disabled, purity is 1.0 (sanity check on the metric)."""
    result = _run_s3(seed=42, config={"n_noise_defects": 0})
    task = result["metrics"]["task"]
    assert task["signal_purity"] == 1.0
    assert task["lot_l_discovered"] is True


def test_s3_consolidation_consumes_retrieval() -> None:
    """IMPROVEMENT R4: the clustering input is what retrieval surfaced; the
    metric records it. With the default top_k all 5 defects are retrieved."""
    result = _run_s3(seed=42)
    task = result["metrics"]["task"]
    assert task["n_defects_retrieved"] == 5  # 3 signal + 2 noise


def test_s3_retrieval_starved_discovery_fails() -> None:
    """Falsifiability (R4): with top_k=1 the search surfaces a single defect,
    below the alert threshold — lot_L discovery must fail even though the
    defects exist in the world."""
    result = _run_s3(seed=42, config={"defect_top_k": 1})
    task = result["metrics"]["task"]
    assert task["n_defects_retrieved"] <= 1
    assert task["lot_l_discovered"] is False


def test_s3_heavy_noise_breaks_discovery() -> None:
    """Falsifiability: enough competing-lot noise to tie/overtake lot_L makes
    discovery FAIL — the metric is computed, not asserted."""
    result = _run_s3(seed=42, config={"n_noise_defects": 6})
    task = result["metrics"]["task"]
    # 6 noise defects split 3/lot_M + 3/lot_N → ties lot_L (3) → no strict win.
    assert task["discovery_margin"] == 0
    assert task["lot_l_discovered"] is False
    assert task["signal_purity"] < 0.5


def test_s3_standing_query_registered() -> None:
    """QualityAgent registers a standing query for lot_L monitoring."""
    result = _run_s3(seed=42)
    task = result["metrics"]["task"]
    assert task["standing_query_registered"] is True
    assert task["quality_steps_completed"] >= 2


def test_s3_federation_finds_cross_instance() -> None:
    """FederatedStore finds defects from all 3 robot instances."""
    result = _run_s3(seed=42)
    task = result["metrics"]["task"]
    assert task["federation_found_all"] is True, (
        "Federated search should find defect atoms from all 3 robots"
    )


def test_s3_consolidation_compresses_and_retains_recall() -> None:
    """H5 (IMPROVEMENT R7): consolidate→dedup→TTL shrinks the store while the
    consolidated memory retains lot_L recall."""
    result = _run_s3(seed=42)
    cons = result["metrics"]["consolidation"]
    assert cons["compression_ratio"] > 0.0, "metabolism must shrink the store"
    assert cons["n_summaries"] >= 1, "lot_L episodes must distill to a summary"
    assert cons["recall_retention"] >= 1.0, "lot_L recall must be retained"


def test_s3_consolidation_no_pruning_when_ttl_huge() -> None:
    """Sanity/falsifiability: with an enormous TTL nothing is pruned, so the
    compression ratio is non-positive (summaries only ADD atoms) — the
    metric is computed, not asserted."""
    result = _run_s3(seed=42, config={"consolidation_max_age": 1e12})
    cons = result["metrics"]["consolidation"]
    assert cons["n_after_ttl"] == cons["n_atoms_before"]
    assert cons["compression_ratio"] <= 0.0


def test_s3_qor_router_exercised() -> None:
    """IMPROVEMENT R8: the QoR router serves both a strict-freshness local
    pre-check and the broad federated discovery query."""
    result = _run_s3(seed=42)
    stats = result["metrics"]["task"]["qor_router"]
    assert stats["local_hits"] >= 1
    assert stats["federated_hits"] >= 1


def test_s3_deterministic() -> None:
    """Same seed produces identical metrics."""
    r1 = _run_s3(seed=99)
    r2 = _run_s3(seed=99)
    assert r1["metrics"]["retrieval"] == r2["metrics"]["retrieval"]
    assert r1["metrics"]["task"] == r2["metrics"]["task"]


def test_s3_prefetch_gives_swarm_local_coverage() -> None:
    """Predictive prefetch (PROJECT §3): the strict-freshness local pre-check
    covers all 3 observers because the task plan prefetched their atoms."""
    result = _run_s3(seed=42)
    pf = result["metrics"]["task"]["prefetch"]
    assert pf["enabled"] is True
    assert pf["prefetched_atoms"] > 0
    assert pf["local_observer_coverage"] == 3


def test_s3_prefetch_disabled_collapses_local_coverage() -> None:
    """Falsifiability: without prefetch, robot_1's local store only answers
    for itself — coverage 1, not 3."""
    result = _run_s3(seed=42, config={"prefetch_enabled": False})
    pf = result["metrics"]["task"]["prefetch"]
    assert pf["prefetched_atoms"] == 0
    assert pf["local_observer_coverage"] == 1
