"""Dose-response sweep runner — measures metrics across heterogeneity levels.

Runs the same task at each heterogeneity dose (from Schema Generator),
collecting round-trip fidelity, calibration, commutativity, and
contract accuracy at each level.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from eval.metamorphic.compositionality import commutativity_divergence
from eval.metrics.contract import round_trip_information_loss
from eval.metrics.se3 import joint_max_error, joint_rmse
from psl.adapters.robots.panda.adapter import PandaAdapter
from psl.adapters.robots.panda.heterogeneous import HeterogeneousPandaAdapter
from psl.ir.translation import translate_r2r
from sim.schema_gen.generator import HETEROGENEITY_DOSES, SchemaTransform, apply_schema_transform
from sim.wrapper import MuJoCoSim


@dataclass
class SweepResult:
    """Result for a single dose level."""

    dose_index: int
    unit_scale: float
    frame_rotation_z_rad: float
    sensor_noise_std: float
    joint_pos_rmse: float
    joint_pos_max_error: float
    information_loss: float
    commutativity_divergence: float
    contract_info_loss: float


@dataclass
class SweepReport:
    """Full sweep report across all doses."""

    seed: int
    n_steps: int
    results: list[SweepResult] = field(default_factory=list)
    wall_time_s: float = 0.0


def run_dose_response_sweep(
    seed: int = 42,
    n_steps: int = 200,
    doses: list[SchemaTransform] | None = None,
) -> SweepReport:
    """Run dose-response sweep across heterogeneity levels.

    For each dose:
      1. Step the sim
      2. Read base state
      3. Apply schema transform (simulate hetero robot's view)
      4. Translate via heterogeneous adapter → IR → base adapter
      5. Measure round-trip fidelity
      6. Measure commutativity divergence

    Args:
        seed: Random seed.
        n_steps: Sim steps before measurement.
        doses: List of schema transforms. Defaults to HETEROGENEITY_DOSES.

    Returns:
        SweepReport with results per dose.
    """
    if doses is None:
        doses = HETEROGENEITY_DOSES

    start_time = time.time()
    rng = np.random.default_rng(seed)
    report = SweepReport(seed=seed, n_steps=n_steps)

    for i, dose in enumerate(doses):
        sim = MuJoCoSim(seed=seed)
        sim.step(n_steps)

        # Base state (canonical)
        jpos = sim.get_joint_positions()
        jvel = sim.get_joint_velocities()
        ee_pos, ee_quat = sim.get_ee_pose()
        base_state: dict[str, object] = {
            "joint_positions": jpos,
            "joint_velocities": jvel,
            "ee_position": ee_pos,
            "ee_quaternion": ee_quat,
            "time": sim.time,
        }

        # Create adapters
        base_adapter = PandaAdapter(entity_id="panda_base")
        hetero_adapter = HeterogeneousPandaAdapter(entity_id="panda_hetero", transform=dose)

        # Simulate what the hetero robot would report
        hetero_state = apply_schema_transform(base_state, dose, rng=rng)

        # Translate: hetero_native → IR → base_native
        translated = translate_r2r(hetero_adapter, base_adapter, hetero_state)

        # Measure round-trip fidelity
        translated_jpos = np.asarray(translated["joint_positions"])
        pos_rmse = joint_rmse(translated_jpos, jpos)
        pos_max = joint_max_error(translated_jpos, jpos)
        info_loss = round_trip_information_loss(jpos, translated_jpos)

        # Commutativity: direct vs multi-hop
        comm_div = commutativity_divergence(hetero_adapter, base_adapter, hetero_state)

        # Contract declared loss
        contract_loss = hetero_adapter.fidelity_contract.information_loss_estimate

        report.results.append(
            SweepResult(
                dose_index=i,
                unit_scale=dose.unit_scale,
                frame_rotation_z_rad=dose.frame_rotation_z_rad,
                sensor_noise_std=dose.sensor_noise_std,
                joint_pos_rmse=pos_rmse,
                joint_pos_max_error=pos_max,
                information_loss=info_loss,
                commutativity_divergence=comm_div,
                contract_info_loss=contract_loss,
            )
        )

    report.wall_time_s = time.time() - start_time
    return report


def save_sweep_report(report: SweepReport, output_dir: str | Path) -> Path:
    """Save sweep report as JSON.

    Args:
        report: The sweep report.
        output_dir: Directory to write to.

    Returns:
        Path to the saved JSON file.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "sweep_results.json"

    data = {
        "seed": report.seed,
        "n_steps": report.n_steps,
        "wall_time_s": report.wall_time_s,
        "results": [asdict(r) for r in report.results],
    }
    path.write_text(json.dumps(data, indent=2))
    return path
