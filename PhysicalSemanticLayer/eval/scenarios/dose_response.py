"""Per-scenario dose-response curves.

Runs a scenario at multiple heterogeneity levels (from its YAML sweep config)
and compares PSL vs baselines B0/B1/B2 at each level. Produces a JSON curve
showing how each method degrades as heterogeneity increases.

This directly tests H1: PSL maintains fidelity while baselines degrade.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from eval.baselines import run_baseline_comparison
from eval.runner.manifest import create_run_directory
from eval.scenarios.config_loader import load_scenario_config
from sim.schema_gen.generator import SchemaTransform


@dataclass
class DoseResponsePoint:
    """One point on the dose-response curve."""

    dose_index: int
    dose_label: str
    unit_scale: float
    noise_std: float
    psl_rmse: float
    b0_rmse: float
    b1_rmse: float
    b2_rmse: float
    psl_info_loss: float
    b1_info_loss: float


def run_scenario_dose_response(
    scenario_slug: str,
    seed: int = 42,
) -> list[DoseResponsePoint]:
    """Run dose-response for a scenario using its YAML sweep config.

    For each dose level, runs the scenario in offline mode and compares
    PSL vs baselines under identical heterogeneity.

    Args:
        scenario_slug: e.g. "s1_mixed_fleet_pick".
        seed: Random seed.

    Returns:
        List of DoseResponsePoint, one per dose level.
    """
    config = load_scenario_config(scenario_slug)
    sweep = config.get("sweep", {})
    if not sweep:
        sweep = {"axis": "schema_dose.units", "values": ["m", "cm", "mm"]}

    values = sweep.get("values", ["m", "cm", "mm"])

    # Map sweep values to SchemaTransform doses
    dose_transforms: list[tuple[str, SchemaTransform]] = []
    for val in values:
        if val == "m":
            dose_transforms.append(("1x (m)", SchemaTransform()))
        elif val == "cm":
            dose_transforms.append(("100x (cm)", SchemaTransform(unit_scale=100.0)))
        elif val == "mm":
            dose_transforms.append(("1000x (mm)", SchemaTransform(unit_scale=1000.0)))
        else:
            # Generic: try to parse as a scale factor
            try:
                scale = float(val)
                dose_transforms.append((f"{scale}x", SchemaTransform(unit_scale=scale)))
            except ValueError:
                dose_transforms.append((str(val), SchemaTransform()))

    # Add noise doses
    dose_transforms.append(("noise=0.01", SchemaTransform(sensor_noise_std=0.01)))
    dose_transforms.append(("noise=0.05", SchemaTransform(sensor_noise_std=0.05)))
    dose_transforms.append(
        ("1000x+noise", SchemaTransform(unit_scale=1000.0, sensor_noise_std=0.01))
    )

    rng = np.random.default_rng(seed)

    # Get a base state from the scenario's MuJoCo scene
    from sim.task_controller import s1_pick_trajectory
    from sim.wrapper import SCENES_DIR, MuJoCoSim

    scene_path = SCENES_DIR / scenario_slug / "scene.xml"
    if not scene_path.exists():
        scene_path = SCENES_DIR / "panda_minimal.xml"

    sim = MuJoCoSim(scene_xml=scene_path, seed=seed)
    from sim.task_controller import execute_trajectory

    trajectory = s1_pick_trajectory()
    readings = execute_trajectory(sim, trajectory)
    final = readings[-1] if readings else {}

    base_state: dict[str, object] = {
        "joint_positions": np.asarray(final.get("joint_positions", np.zeros(7))),
        "joint_velocities": np.asarray(final.get("joint_velocities", np.zeros(7))),
        "ee_position": np.asarray(final.get("ee_position", np.zeros(3))),
        "ee_quaternion": np.asarray(final.get("ee_quaternion", np.array([1, 0, 0, 0]))),
        "time": final.get("time", 0.0),
    }

    results: list[DoseResponsePoint] = []
    for i, (label, dose) in enumerate(dose_transforms):
        comparison = run_baseline_comparison(base_state, dose, rng)

        results.append(
            DoseResponsePoint(
                dose_index=i,
                dose_label=label,
                unit_scale=dose.unit_scale,
                noise_std=dose.sensor_noise_std,
                psl_rmse=comparison.get("PSL", {}).get("joint_rmse", 0.0),
                b0_rmse=comparison.get("B0", {}).get("joint_rmse", 0.0),
                b1_rmse=comparison.get("B1", {}).get("joint_rmse", 0.0),
                b2_rmse=comparison.get("B2", {}).get("joint_rmse", 0.0),
                psl_info_loss=comparison.get("PSL", {}).get("info_loss", 0.0),
                b1_info_loss=comparison.get("B1", {}).get("info_loss", 0.0),
            )
        )

    return results


def save_dose_response(
    scenario_slug: str,
    points: list[DoseResponsePoint],
) -> Path:
    """Save dose-response results to a timestamped run directory."""
    run_dir = create_run_directory("experiments/runs", f"dose-{scenario_slug}")
    path = run_dir / "dose_response.json"
    data = {
        "scenario": scenario_slug,
        "n_doses": len(points),
        "points": [asdict(p) for p in points],
    }
    path.write_text(json.dumps(data, indent=2))
    return path


def main() -> None:
    """CLI entry point for per-scenario dose-response."""
    scenario = os.environ.get("PSL_SCENARIO", "s1_mixed_fleet_pick")
    seed = int(os.environ.get("PSL_SEED", "42"))

    print(f"Running dose-response for {scenario} (seed={seed})")
    points = run_scenario_dose_response(scenario, seed=seed)
    path = save_dose_response(scenario, points)
    print(f"Results saved to {path}")
    print()
    print(f"{'Dose':<15} {'PSL RMSE':>12} {'B0 RMSE':>12} {'B1 RMSE':>12} {'B2 RMSE':>12}")
    print("-" * 65)
    for p in points:
        print(
            f"{p.dose_label:<15} {p.psl_rmse:>12.2e} {p.b0_rmse:>12.2e} {p.b1_rmse:>12.2e} {p.b2_rmse:>12.2e}"
        )


if __name__ == "__main__":
    main()
