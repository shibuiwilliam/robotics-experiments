"""Tests for clock skew stress test with realistic uncertainty model."""

from __future__ import annotations

import pytest

from eval.stress.clock_skew import sweep_clock_skew


@pytest.mark.unit
class TestClockSkewStress:
    def test_zero_skew_no_violations(self) -> None:
        """Zero skew should produce zero violations regardless of uncertainty."""
        results = sweep_clock_skew([0.0], seed=42, clock_uncertainty=0.005)
        assert len(results) == 1
        assert results[0].causal_violations == 0
        assert results[0].gate_rejections == 0

    def test_small_skew_within_tolerance_accepted(self) -> None:
        """1ms skew with 5ms uncertainty should be accepted (normal NTP jitter)."""
        results = sweep_clock_skew([0.001], seed=42, clock_uncertainty=0.005)
        assert results[0].gate_rejections == 0, (
            "1ms skew within 5ms NTP tolerance should be accepted"
        )

    def test_large_skew_beyond_tolerance_rejected(self) -> None:
        """50ms skew with 5ms uncertainty should be rejected (genuine violation)."""
        results = sweep_clock_skew([0.050], seed=42, clock_uncertainty=0.005)
        assert results[0].gate_rejections > 0, (
            "50ms skew far exceeding 5ms tolerance must be rejected"
        )

    def test_boundary_transition(self) -> None:
        """Acceptance transitions from accept to reject as skew increases past uncertainty."""
        results = sweep_clock_skew(
            [0.0, 0.001, 0.003, 0.005, 0.010, 0.050],
            seed=42,
            clock_uncertainty=0.005,
        )
        # Low skew (0, 1ms, 3ms): within 5ms tolerance → accepted
        assert results[0].gate_rejections == 0  # 0ms
        assert results[1].gate_rejections == 0  # 1ms < 5ms
        assert results[2].gate_rejections == 0  # 3ms < 5ms
        # High skew (50ms): far beyond 5ms tolerance → rejected
        assert results[-1].gate_rejections > 0  # 50ms >> 5ms

    def test_sweep_returns_correct_count(self) -> None:
        skews = [0.0, 0.001, 0.01, 0.05]
        results = sweep_clock_skew(skews, seed=42, clock_uncertainty=0.005)
        assert len(results) == len(skews)
        for r, s in zip(results, skews, strict=True):
            assert r.skew_seconds == s
            assert r.clock_uncertainty == 0.005

    def test_rejection_rate_field(self) -> None:
        """Verify rejection_rate is computed correctly."""
        results = sweep_clock_skew([0.0, 0.100], seed=42, clock_uncertainty=0.005)
        assert results[0].rejection_rate == 0.0
        assert results[1].rejection_rate > 0.0
        assert results[1].rejection_rate <= 1.0
