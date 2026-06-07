"""Tests for clock skew stress test."""

from __future__ import annotations

import pytest

from eval.stress.clock_skew import sweep_clock_skew


@pytest.mark.unit
class TestClockSkewStress:
    def test_zero_skew_no_violations(self) -> None:
        results = sweep_clock_skew([0.0], seed=42)
        assert len(results) == 1
        assert results[0].causal_violations == 0
        assert results[0].gate_rejections == 0

    def test_large_skew_causes_violations(self) -> None:
        results = sweep_clock_skew([5.0], seed=42)
        assert len(results) == 1
        r = results[0]
        # Large skew should cause causal ordering violation
        assert r.gate_rejections > 0 or r.causal_violations > 0

    def test_monotonicity(self) -> None:
        """Violations should be non-decreasing with increasing skew."""
        results = sweep_clock_skew([0.0, 0.01, 0.1, 1.0, 5.0], seed=42)
        rejections = [r.gate_rejections for r in results]
        # Non-decreasing (allow ties)
        for i in range(1, len(rejections)):
            assert rejections[i] >= rejections[i - 1], (
                f"Rejections decreased: {rejections[i - 1]} -> {rejections[i]} "
                f"at skew {results[i].skew_seconds}"
            )

    def test_sweep_returns_correct_count(self) -> None:
        skews = [0.0, 0.001, 0.01, 0.05]
        results = sweep_clock_skew(skews, seed=42)
        assert len(results) == len(skews)
        for r, s in zip(results, skews, strict=True):
            assert r.skew_seconds == s
