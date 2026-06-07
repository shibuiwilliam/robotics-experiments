"""Oracle evaluation tests — round-trip fidelity with ground truth."""

from __future__ import annotations

import pytest

from eval.oracle.round_trip import run_round_trip_eval
from psl.adapters.robots.panda.adapter import PandaAdapter
from sim.wrapper import MuJoCoSim


@pytest.mark.oracle
class TestRoundTripEval:
    """Run the full oracle evaluation pipeline and check metrics."""

    def test_round_trip_metrics(self, sim: MuJoCoSim, adapter: PandaAdapter) -> None:
        metrics = run_round_trip_eval(sim, adapter, n_steps=100)

        # Round-trip should be near-lossless for the Panda adapter
        assert metrics["joint_pos_rmse"] < 1e-10, (
            f"Joint RMSE too high: {metrics['joint_pos_rmse']:.2e}"
        )
        assert metrics["joint_pos_max_error"] < 1e-10, (
            f"Joint max error too high: {metrics['joint_pos_max_error']:.2e}"
        )
        assert metrics["joint_vel_rmse"] < 1e-10, (
            f"Velocity RMSE too high: {metrics['joint_vel_rmse']:.2e}"
        )
        assert metrics["information_loss"] < 1e-8, (
            f"Information loss too high: {metrics['information_loss']:.2e}"
        )

    def test_multiple_timesteps(self, sim: MuJoCoSim, adapter: PandaAdapter) -> None:
        """Run round-trip at different sim times — should always be near-lossless."""
        for n_steps in [10, 50, 200, 500]:
            fresh_sim = MuJoCoSim(seed=42)
            metrics = run_round_trip_eval(fresh_sim, adapter, n_steps=n_steps)
            assert metrics["joint_pos_rmse"] < 1e-10, (
                f"Failed at {n_steps} steps: RMSE={metrics['joint_pos_rmse']:.2e}"
            )
