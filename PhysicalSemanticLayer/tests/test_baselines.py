"""Baseline comparison tests."""

from __future__ import annotations

import numpy as np
import pytest

from eval.baselines import run_baseline_comparison
from sim.schema_gen.generator import SchemaTransform
from sim.wrapper import MuJoCoSim

SEED = 42


def _make_state() -> dict[str, object]:
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
class TestBaselines:
    def test_noiseless_baselines(self) -> None:
        """With noiseless transform, B0 and PSL should both be perfect.
        B1 (raw blackboard) should have error when transform is non-trivial."""
        state = _make_state()
        t = SchemaTransform(unit_scale=1000.0)
        rng = np.random.default_rng(SEED)

        results = run_baseline_comparison(state, t, rng)

        # B0 and PSL both know the transform → near-zero error
        assert results["B0"]["joint_rmse"] < 1e-10
        assert results["PSL"]["joint_rmse"] < 1e-10

        # B1 (raw) doesn't translate → high error for scaled data
        assert results["B1"]["joint_rmse"] > 0.1

        # B2 (realistic LLM sim) has small imprecision noise
        assert results["B2"]["joint_rmse"] < 0.01, "B2 should be close but not perfect"
        assert results["B2"]["joint_rmse"] > 0, "B2 should have non-zero error (realistic noise)"

    def test_noisy_baselines(self) -> None:
        """With noisy transform, all methods should degrade, but B1 worst."""
        state = _make_state()
        # Unit scaling + noise: B1 sees scaled+noisy values without any translation
        t = SchemaTransform(unit_scale=1000.0, sensor_noise_std=0.05)
        rng = np.random.default_rng(SEED)

        results = run_baseline_comparison(state, t, rng)

        # B0/B2/PSL know the transform, but noise is irreversible → some error
        assert results["PSL"]["joint_rmse"] > 1e-6

        # B1 (raw) sees 1000x-scaled data as-is → massive error
        assert results["B1"]["joint_rmse"] > results["PSL"]["joint_rmse"]

    def test_identity_all_perfect(self) -> None:
        """With identity transform, everyone should be perfect."""
        state = _make_state()
        t = SchemaTransform()
        rng = np.random.default_rng(SEED)

        results = run_baseline_comparison(state, t, rng)
        # B0, B1, PSL are deterministic → perfect on identity
        for name in ["B0", "B1", "PSL"]:
            assert results[name]["joint_rmse"] < 1e-10, f"{name} failed on identity"
        # B2 has small numerical noise even on identity transform
        assert results["B2"]["joint_rmse"] < 0.01, "B2 should be close on identity"
