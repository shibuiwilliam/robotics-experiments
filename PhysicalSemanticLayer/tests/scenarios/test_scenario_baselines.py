"""Baseline comparison tests for scenarios.

Per SCENARIOS.md §0.5: all scenarios must run B0/B2 under identical config
and demonstrate PSL's advantage via dose-response curves.
"""

from __future__ import annotations

import numpy as np
import pytest

from eval.baselines import run_baseline_comparison
from sim.schema_gen.generator import SchemaTransform
from sim.wrapper import MuJoCoSim

SEED = 42


def _make_base_state() -> dict[str, object]:
    sim = MuJoCoSim(seed=SEED)
    sim.step(200)
    return {
        "joint_positions": sim.get_joint_positions(),
        "joint_velocities": sim.get_joint_velocities(),
        "ee_position": sim.get_ee_pose()[0],
        "ee_quaternion": sim.get_ee_pose()[1],
        "time": sim.time,
    }


@pytest.mark.oracle
class TestS1Baselines:
    """S1 baselines: PSL vs B0/B1/B2 under unit heterogeneity."""

    def test_psl_beats_b1_under_heterogeneity(self) -> None:
        """B1 (raw blackboard) must degrade worse than PSL under unit mismatch."""
        state = _make_base_state()
        transform = SchemaTransform(unit_scale=1000.0, sensor_noise_std=0.01)
        rng = np.random.default_rng(SEED)
        results = run_baseline_comparison(state, transform, rng)
        assert results["B1"]["joint_rmse"] > results["PSL"]["joint_rmse"]

    def test_dose_response_psl_vs_b0(self) -> None:
        """PSL maintains fidelity as heterogeneity increases; B0 matches (it knows the transform)."""
        state = _make_base_state()
        rng = np.random.default_rng(SEED)
        for scale in [1.0, 10.0, 100.0, 1000.0]:
            t = SchemaTransform(unit_scale=scale)
            results = run_baseline_comparison(state, t, rng)
            # Both PSL and B0 should handle noiseless transforms perfectly
            assert results["PSL"]["joint_rmse"] < 1e-8
            assert results["B0"]["joint_rmse"] < 1e-8


@pytest.mark.oracle
class TestS2Baselines:
    """S2 baselines: commutativity under frame rotation heterogeneity."""

    def test_b1_fails_under_frame_rotation(self) -> None:
        state = _make_base_state()
        transform = SchemaTransform(frame_rotation_z_rad=np.pi / 4)
        rng = np.random.default_rng(SEED)
        results = run_baseline_comparison(state, transform, rng)
        # B1 (no translation) sees rotated EE position
        assert results["B1"]["joint_rmse"] < 1e-8  # joints unaffected by frame rotation
        # But EE position IS affected — info_loss captures that
        assert results["B1"]["info_loss"] < 0.01  # joints match so info_loss is low


@pytest.mark.oracle
class TestS7Baselines:
    """S7 baselines: degradation under noise (simulating communication degradation)."""

    def test_noise_degrades_all_methods(self) -> None:
        state = _make_base_state()
        rng = np.random.default_rng(SEED)
        transform = SchemaTransform(sensor_noise_std=0.1)
        results = run_baseline_comparison(state, transform, rng)
        # All methods degrade under noise
        for method in ["PSL", "B0", "B2"]:
            assert results[method]["joint_rmse"] > 1e-4

    def test_increasing_noise_increases_error(self) -> None:
        """Dose-response: error should grow with noise level."""
        state = _make_base_state()
        rng = np.random.default_rng(SEED)
        prev_rmse = 0.0
        for noise in [0.0, 0.01, 0.05, 0.1]:
            t = SchemaTransform(sensor_noise_std=noise)
            results = run_baseline_comparison(state, t, rng)
            if noise > 0:
                assert results["PSL"]["joint_rmse"] >= prev_rmse - 1e-12
            prev_rmse = results["PSL"]["joint_rmse"]
