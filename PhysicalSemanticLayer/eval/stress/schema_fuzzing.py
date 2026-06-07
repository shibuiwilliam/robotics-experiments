"""Schema fuzzing — randomized heterogeneity stress test.

Generates random SchemaTransform parameters from a wide range,
applies them to the R2R pipeline, and records where PSL breaks.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from eval.metrics.contract import round_trip_information_loss
from eval.metrics.se3 import joint_rmse
from psl.adapters.robots.panda.adapter import PandaAdapter
from psl.adapters.robots.panda.heterogeneous import HeterogeneousPandaAdapter
from psl.ir.translation import translate_r2r
from sim.schema_gen.generator import SchemaTransform, apply_schema_transform
from sim.wrapper import MuJoCoSim


@dataclass(frozen=True)
class FuzzResult:
    """Result of a single fuzz run."""

    unit_scale: float
    frame_rotation_z_rad: float
    sensor_noise_std: float
    joint_rmse: float
    info_loss: float
    commutativity_div: float
    gate_accepted: bool
    contract_valid: bool


def fuzz_schema(
    n_samples: int,
    seed: int,
    sim: MuJoCoSim,
    adapter: PandaAdapter,
) -> list[FuzzResult]:
    """Run randomized schema fuzzing.

    Args:
        n_samples: Number of random transforms to test.
        seed: Random seed.
        sim: Stepped MuJoCo simulation.
        adapter: Base Panda adapter.

    Returns:
        List of FuzzResult for each sample.
    """
    rng = np.random.default_rng(seed)

    base_state: dict[str, object] = {
        "joint_positions": sim.get_joint_positions(),
        "joint_velocities": sim.get_joint_velocities(),
        "ee_position": sim.get_ee_pose()[0],
        "ee_quaternion": sim.get_ee_pose()[1],
        "time": sim.time,
    }
    original_jpos = np.asarray(base_state["joint_positions"])

    results: list[FuzzResult] = []

    for _ in range(n_samples):
        unit_scale = float(10 ** rng.uniform(-2, 4))  # 0.01 to 10000
        frame_rot = float(rng.uniform(0, 2 * np.pi))
        noise_std = float(rng.uniform(0, 0.1))

        transform = SchemaTransform(
            unit_scale=unit_scale,
            frame_rotation_z_rad=frame_rot,
            sensor_noise_std=noise_std,
        )

        hetero_state = apply_schema_transform(base_state, transform, rng=rng)

        # PSL translation via heterogeneous adapter
        hetero_adapter = HeterogeneousPandaAdapter(entity_id="fuzz_h", transform=transform)
        base_adapter = PandaAdapter(entity_id="fuzz_b")

        try:
            psl_result = translate_r2r(hetero_adapter, base_adapter, hetero_state)
            psl_jpos = np.asarray(psl_result["joint_positions"])
            rmse = joint_rmse(psl_jpos, original_jpos)
            info = round_trip_information_loss(original_jpos, psl_jpos)
        except Exception:
            rmse = float("inf")
            info = 1.0

        # Commutativity: translate via intermediate
        try:
            from eval.metamorphic.compositionality import commutativity_divergence

            comm = commutativity_divergence(hetero_adapter, base_adapter, hetero_state)
        except Exception:
            comm = float("inf")

        # Contract check
        contract = hetero_adapter.fidelity_contract
        contract_valid = contract.information_loss_estimate >= info * 0.5

        # Gate check (simplified: check if RMSE is within physical bounds)
        gate_accepted = rmse < 1.0

        results.append(
            FuzzResult(
                unit_scale=unit_scale,
                frame_rotation_z_rad=frame_rot,
                sensor_noise_std=noise_std,
                joint_rmse=rmse,
                info_loss=info,
                commutativity_div=comm,
                gate_accepted=gate_accepted,
                contract_valid=contract_valid,
            )
        )

    return results


def find_breaking_point(results: list[FuzzResult]) -> dict[str, float]:
    """Identify thresholds where PSL starts degrading.

    Returns:
        Dict with estimated breaking thresholds per dimension.
    """
    rmse_threshold = 0.01

    noise_values = sorted(set(r.sensor_noise_std for r in results))
    noise_break = max(noise_values) if noise_values else 0.0
    for nv in noise_values:
        matching = [r for r in results if abs(r.sensor_noise_std - nv) < 1e-9]
        avg_rmse = np.mean([r.joint_rmse for r in matching]) if matching else 0.0
        if avg_rmse > rmse_threshold:
            noise_break = nv
            break

    # Gate rejection threshold
    rejected = [r for r in results if not r.gate_accepted]
    gate_break_rmse = min(r.joint_rmse for r in rejected) if rejected else float("inf")

    return {
        "noise_breaking_std": noise_break,
        "gate_rejection_min_rmse": gate_break_rmse,
        "n_gate_rejected": len(rejected),
        "n_contract_invalid": sum(1 for r in results if not r.contract_valid),
        "max_rmse": max(r.joint_rmse for r in results) if results else 0.0,
        "max_info_loss": max(r.info_loss for r in results) if results else 0.0,
    }
