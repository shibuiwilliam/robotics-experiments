"""Acceptance tests for Scenario 2: Physical vs Record Reconciliation.

From SCENARIOS.md:
- Multi-observer fusion beats single observer (H9)
- Discrepancy ticket generated for WMS drift
- Write-back corrects WMS to fusion estimate
- Deterministic under fixed seed

After remediation (IMPROVEMENT.md C1): observed counts are DRAWN from
ground-truth + seeded Gaussian noise, and the H9 verdict is MEASURED by a
Monte-Carlo over many seeded draws. These tests therefore include
falsifiability checks: with the wrong (equal) weighting, fusion does NOT beat
the best single observer.
"""

from mws.core.config import MWSSettings
from mws.core.types import CloudMode
from mws.scenarios.registry import get_scenario
from mws.scenarios.s2_physical_record_reconciliation.scenario import (
    DEFAULTS,
    monte_carlo_fusion_advantage,
    weighted_fuse,
)


def _run_s2(seed: int = 0, config: dict | None = None) -> dict:
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=seed)
    scenario = get_scenario("physical_record_reconciliation")
    return scenario.run(seed=seed, config=config or {}, settings=settings)


def test_s2_completes_mock() -> None:
    """Gate: scenario run completes in mock mode with no keys."""
    result = _run_s2(seed=42)
    assert "run_id" in result
    assert "metrics" in result
    assert "fusion" in result["metrics"]
    assert "discrepancy" in result["metrics"]
    assert "write_back" in result["metrics"]


def test_s2_fusion_beats_single_observer() -> None:
    """H9: provenance(precision)-weighted fusion beats the best single observer.

    This is now a statistical verdict over many seeded draws, not a constant.
    """
    result = _run_s2(seed=42)
    fusion = result["metrics"]["fusion"]
    assert fusion["fusion_beats_single"] is True, (
        f"mean fusion error {fusion['statistical']['mean_fusion_error']:.3f} should be "
        f"less than mean best-single error {fusion['statistical']['mean_best_single_error']:.3f}"
    )
    # Fusion estimate should sit near ground truth (30), within a few sigma.
    assert 27.0 <= fusion["fusion_estimate"] <= 33.0
    # The single-draw estimate is noisy; the robust claim is the statistical one.
    stat = fusion["statistical"]
    assert stat["mean_fusion_error"] < stat["mean_best_single_error"]


def test_s2_equal_weighting_does_not_beat_single() -> None:
    """Falsifiability: with the WRONG (equal) weighting, fusion loses to the
    best single observer. Proves the verdict is computed, and that correct
    inverse-variance weighting is load-bearing — not a rigged True."""
    result = _run_s2(seed=42)
    stat = result["metrics"]["fusion"]["statistical"]
    assert stat["equal_weighting_beats_single"] is False, (
        "Equal weighting should NOT beat the best single observer for these "
        "asymmetric sensors; if it does, the experiment is not discriminating."
    )


def test_s2_noise_drives_counts() -> None:
    """Different seeds produce different observations and fusion estimates —
    i.e. the result is not a hard-coded constant (29.857...)."""
    r1 = _run_s2(seed=1)
    r2 = _run_s2(seed=2)
    e1 = r1["metrics"]["fusion"]["fusion_estimate"]
    e2 = r2["metrics"]["fusion"]["fusion_estimate"]
    assert e1 != e2, "Fusion estimate must vary with the noise seed"


def test_s2_discrepancy_ticket_generated() -> None:
    """A discrepancy ticket is generated when WMS count diverges from fusion."""
    result = _run_s2(seed=42)
    disc = result["metrics"]["discrepancy"]
    assert disc["ticket_generated"] is True
    # WMS shows 50, fusion ~30 → discrepancy should be ~20
    assert disc["discrepancy_amount"] > 15.0
    assert disc["wms_count"] == 50


def test_s2_write_back_correct() -> None:
    """Write-back corrects WMS toward ground truth via the fusion estimate."""
    result = _run_s2(seed=42)
    wb = result["metrics"]["write_back"]
    assert wb["applied"] is True
    assert wb["old_count"] == 50
    # New count is the rounded fusion estimate, near ground truth (30).
    assert 28 <= wb["new_count"] <= 32
    # Correction is far better than the 50→? drift; within a couple of units.
    assert wb["write_back_error"] <= 2


def test_s2_deterministic() -> None:
    """Same seed produces identical metrics."""
    r1 = _run_s2(seed=99)
    r2 = _run_s2(seed=99)
    assert r1["metrics"]["fusion"] == r2["metrics"]["fusion"]
    assert r1["metrics"]["discrepancy"] == r2["metrics"]["discrepancy"]
    assert r1["metrics"]["write_back"] == r2["metrics"]["write_back"]


# --- Protocol gating (IMPROVEMENT R3) ---


def test_s2_fusion_inputs_come_from_retrieval() -> None:
    """The fusion consumed observations surfaced by the recon query — the
    metric records how many observer readings retrieval provided."""
    result = _run_s2(seed=42)
    fusion = result["metrics"]["fusion"]
    assert fusion["fusion_failed"] is False
    assert fusion["n_observations_retrieved"] == 2


def test_s2_unretrievable_observations_fail_fusion() -> None:
    """Falsifiability: if the observer atoms never reach the store, retrieval
    cannot surface them and the fusion — and therefore H9 — must fail."""
    result = _run_s2(seed=42, config={"suppress_observations": True})
    fusion = result["metrics"]["fusion"]
    assert fusion["fusion_failed"] is True
    assert fusion["n_observations_retrieved"] == 0
    assert fusion["fusion_beats_single"] is False
    assert result["metrics"]["write_back"]["applied"] is False
    assert result["metrics"]["discrepancy"]["ticket_generated"] is False


# --- Pure helper unit tests ---


def test_weighted_fuse_matches_inverse_variance() -> None:
    """weighted_fuse is a plain precision-weighted average."""
    # equal weights → simple mean
    assert weighted_fuse([10.0, 20.0], [1.0, 1.0]) == 15.0
    # double weight on first → pulled toward it
    assert weighted_fuse([10.0, 20.0], [3.0, 1.0]) == 12.5


def test_monte_carlo_invvar_beats_equal() -> None:
    """The default (inverse-variance) weighting beats single; equal does not,
    for the default asymmetric sensors."""
    inv = monte_carlo_fusion_advantage(
        DEFAULTS["ground_truth_count"],
        [DEFAULTS["sigma_a"], DEFAULTS["sigma_b"]],
        int(DEFAULTS["n_fusion_trials"]),
        seed=7,
    )
    eq = monte_carlo_fusion_advantage(
        DEFAULTS["ground_truth_count"],
        [DEFAULTS["sigma_a"], DEFAULTS["sigma_b"]],
        int(DEFAULTS["n_fusion_trials"]),
        seed=7,
        weights=[1.0, 1.0],
    )
    assert inv["fusion_beats_single"] is True
    assert eq["fusion_beats_single"] is False
