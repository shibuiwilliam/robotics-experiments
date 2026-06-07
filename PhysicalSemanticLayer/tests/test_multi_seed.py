"""Tests for multi-seed evaluation framework."""

from __future__ import annotations

import pytest

from eval.scenarios.base import BreakpointResult, ScenarioResult
from eval.scenarios.multi_seed import MultiSeedResult, run_multi_seed


def _make_dummy_result(seed: int, **kwargs: object) -> ScenarioResult:
    """Create a minimal ScenarioResult for testing."""
    return ScenarioResult(
        scenario_id="test_scenario",
        seed=seed,
        business_success=True,
        psl_success=True,
        breakpoints=[
            BreakpointResult(
                name="test_bp",
                triggered=True,
                metric_value=0.001 * seed,
                threshold=0.1,
                passed=True,
            )
        ],
        metrics={
            "rmse": 0.001 * seed,
            "info_loss": 0.01 * seed,
        },
        rq_contributions=["RQ1"],
    )


def _dummy_evaluate(seed: int = 42, **kwargs: object) -> ScenarioResult:
    return _make_dummy_result(seed)


@pytest.mark.unit
class TestMultiSeed:
    def test_run_with_3_seeds(self) -> None:
        result = run_multi_seed(_dummy_evaluate, seeds=[42, 43, 44])
        assert isinstance(result, MultiSeedResult)
        assert result.n_seeds == 3
        assert len(result.results) == 3
        assert result.all_business_success
        assert result.all_psl_success

    def test_produces_mean_and_std(self) -> None:
        result = run_multi_seed(_dummy_evaluate, seeds=[42, 43, 44])
        assert "rmse" in result.metrics_mean
        assert "rmse" in result.metrics_std
        assert result.metrics_mean["rmse"] > 0

    def test_produces_ci95(self) -> None:
        result = run_multi_seed(_dummy_evaluate, seeds=[42, 43, 44])
        assert "rmse" in result.metrics_ci95
        lo, hi = result.metrics_ci95["rmse"]
        mean = result.metrics_mean["rmse"]
        assert lo <= mean <= hi

    def test_ci_narrows_with_more_seeds(self) -> None:
        r3 = run_multi_seed(_dummy_evaluate, seeds=[42, 43, 44])
        r5 = run_multi_seed(_dummy_evaluate, seeds=[42, 43, 44, 45, 46])
        w3 = r3.metrics_ci95["rmse"][1] - r3.metrics_ci95["rmse"][0]
        w5 = r5.metrics_ci95["rmse"][1] - r5.metrics_ci95["rmse"][0]
        assert w5 <= w3

    def test_all_seeds_produce_valid_results(self) -> None:
        seeds = [10, 20, 30, 40, 50]
        result = run_multi_seed(_dummy_evaluate, seeds=seeds)
        assert all(r.overall_pass for r in result.results)
        assert result.seeds == seeds
