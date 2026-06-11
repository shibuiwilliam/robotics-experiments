"""Tests for multi-seed runner."""

from mws.core.config import MWSSettings
from mws.core.types import CloudMode
from mws.eval.multi_seed import run_multi_seed


def test_multi_seed_runs_and_aggregates() -> None:
    """Run S1 with 3 seeds, verify summary statistics are computed."""
    settings = MWSSettings(cloud_mode=CloudMode.MOCK, seed=0)
    result = run_multi_seed(
        "maintenance_handoff",
        seeds=[0, 1, 2],
        settings=settings,
    )
    assert result["n_seeds"] == 3
    assert len(result["per_seed"]) == 3
    assert "summary" in result

    # Check that retrieval metrics have CI
    summary = result["summary"]
    assert "retrieval.recall_at_10" in summary
    stats = summary["retrieval.recall_at_10"]
    assert "mean" in stats
    assert "ci95_low" in stats
    assert "ci95_high" in stats
    assert stats["ci95_low"] <= stats["mean"] <= stats["ci95_high"]
