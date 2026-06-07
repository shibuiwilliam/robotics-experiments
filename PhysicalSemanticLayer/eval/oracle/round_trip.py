"""Oracle test: round-trip fidelity through the Panda adapter.

Tests native → IR → native round-trip using MuJoCo ground truth.
This module is used by pytest tests marked @pytest.mark.oracle.

Ground truth access: YES (this is eval/ — allowed).
"""

from __future__ import annotations

import numpy as np

from eval.metrics.calibration import regression_ece, scalar_calibration_nll
from eval.metrics.contract import round_trip_information_loss
from eval.metrics.se3 import joint_max_error, joint_rmse
from psl.adapters.robots.panda.adapter import PandaAdapter
from sim.wrapper import MuJoCoSim


def run_round_trip_eval(
    sim: MuJoCoSim,
    adapter: PandaAdapter,
    n_steps: int = 100,
) -> dict[str, float]:
    """Run round-trip eval: read sensors → to_ir → from_ir → compare to sensor.

    Args:
        sim: Running MuJoCo simulation.
        adapter: Panda adapter instance.
        n_steps: Number of sim steps before measuring.

    Returns:
        Dict of metric name → value.
    """
    # Step the simulation
    sim.step(n_steps)

    # Read sensor data (what the adapter sees)
    jpos = sim.get_joint_positions()
    jvel = sim.get_joint_velocities()
    ee_pos, ee_quat = sim.get_ee_pose()

    native_state: dict[str, object] = {
        "joint_positions": jpos,
        "joint_velocities": jvel,
        "ee_position": ee_pos,
        "ee_quaternion": ee_quat,
        "time": sim.time,
    }

    # Round trip: native → IR → native
    ir_state = adapter.to_ir(native_state)
    reconstructed = adapter.from_ir(ir_state)

    recon_jpos = np.asarray(reconstructed["joint_positions"])
    recon_jvel = np.asarray(reconstructed["joint_velocities"])

    # Compute metrics
    pos_rmse = joint_rmse(recon_jpos, jpos)
    pos_max_err = joint_max_error(recon_jpos, jpos)
    vel_rmse = joint_rmse(recon_jvel, jvel)
    info_loss = round_trip_information_loss(jpos, recon_jpos)

    # Calibration: check declared covariance vs actual error
    # For round-trip, the error should be near-zero, so calibration
    # checks whether the declared noise std brackets the actual error.
    from psl.adapters.robots.panda.adapter import JOINT_POS_NOISE_STD

    declared_var = np.full(7, JOINT_POS_NOISE_STD**2)
    declared_std = np.full(7, JOINT_POS_NOISE_STD)
    cal_nll = scalar_calibration_nll(recon_jpos, jpos, declared_var)
    cal_ece = regression_ece(recon_jpos, jpos, declared_std)

    return {
        "joint_pos_rmse": pos_rmse,
        "joint_pos_max_error": pos_max_err,
        "joint_vel_rmse": vel_rmse,
        "information_loss": info_loss,
        "calibration_nll": cal_nll,
        "calibration_ece": cal_ece,
    }
