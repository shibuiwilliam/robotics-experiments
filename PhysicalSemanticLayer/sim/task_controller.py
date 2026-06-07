"""Task controllers — drive MuJoCo sim through scenario-specific trajectories.

Each controller produces a sequence of actuator targets and steps the sim,
reading ONLY sensor data (never ground truth). The controller does NOT
participate in PSL translation — it just moves the robot.

Ground truth is read separately in eval/ for measurement.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from sim.wrapper import MuJoCoSim


@dataclass(frozen=True)
class TaskStep:
    """One step in a task trajectory.

    Fields:
        name: Human-readable step label (e.g. "approach_bin_c").
        joint_targets: Target joint positions for arm actuators (rad).
        hold_steps: Number of sim steps to hold at this target.
    """

    name: str
    joint_targets: NDArray[np.float64]
    hold_steps: int = 500


def execute_trajectory(
    sim: MuJoCoSim,
    steps: list[TaskStep],
    sensor_prefix: str = "",
) -> list[dict[str, object]]:
    """Execute a task trajectory and return sensor readings at each step.

    Drives the sim by setting position actuator targets and stepping.
    Returns the sensor state at each waypoint AFTER convergence.

    Does NOT read ground truth — only sensor data.

    Args:
        sim: The MuJoCo simulation (already loaded with scene).
        steps: Ordered list of task steps.
        sensor_prefix: Sensor name prefix (e.g. "a_" for dual-arm scenes).

    Returns:
        List of sensor-state dicts, one per step.
    """
    readings: list[dict[str, object]] = []
    n_actuators = sim.model.nu

    for step in steps:
        # Set actuator targets (only for arm joints, pad with zeros for others)
        ctrl = np.zeros(n_actuators)
        n_targets = min(len(step.joint_targets), n_actuators)
        ctrl[:n_targets] = step.joint_targets[:n_targets]
        sim.set_control(ctrl)

        # Step simulation to let the arm converge
        sim.step(step.hold_steps)

        # Read sensor state (NO ground truth)
        jpos = sim.get_joint_positions(sensor_prefix)
        jvel = sim.get_joint_velocities(sensor_prefix)
        ee_pos, ee_quat = sim.get_ee_pose(sensor_prefix)

        readings.append(
            {
                "step_name": step.name,
                "joint_positions": jpos,
                "joint_velocities": jvel,
                "ee_position": ee_pos,
                "ee_quaternion": ee_quat,
                "time": sim.time,
            }
        )

    return readings


# ── Pre-defined trajectories for each scenario ──


def s1_pick_trajectory() -> list[TaskStep]:
    """S1: Move to bin C, grasp, retract, move to handoff position.

    Joint targets are approximate waypoints for a 7-DOF Panda
    reaching positions on a table-top workspace.
    """
    return [
        TaskStep("home", np.array([0.0, -0.3, 0.0, -1.5, 0.0, 1.2, 0.0]), 300),
        TaskStep("approach_bin_c", np.array([0.5, -0.2, 0.3, -1.8, 0.1, 1.5, 0.2]), 400),
        TaskStep("grasp_pose", np.array([0.5, 0.0, 0.3, -2.0, 0.1, 1.8, 0.2]), 300),
        TaskStep("retract", np.array([0.3, -0.3, 0.0, -1.5, 0.0, 1.2, 0.0]), 300),
        TaskStep("handoff_position", np.array([0.0, -0.5, 0.0, -1.2, 0.0, 0.8, 0.0]), 300),
    ]


def s2_changeover_trajectory() -> list[TaskStep]:
    """S2: Move to fixture, apply recipe config, retract."""
    return [
        TaskStep("home", np.array([0.0, -0.3, 0.0, -1.5, 0.0, 1.2, 0.0]), 300),
        TaskStep("fixture_approach", np.array([0.3, -0.1, 0.2, -1.8, 0.0, 1.5, 0.1]), 400),
        TaskStep("recipe_config", np.array([0.5, -0.3, 0.2, -1.5, 0.0, 1.0, 0.3]), 300),
        TaskStep("retract", np.array([0.0, -0.3, 0.0, -1.5, 0.0, 1.2, 0.0]), 300),
    ]


def s3_custody_trajectory() -> list[TaskStep]:
    """S3: Pick sample, transfer to rack, move to reader position."""
    return [
        TaskStep("home", np.array([0.0, -0.3, 0.0, -1.5, 0.0, 1.2, 0.0]), 300),
        TaskStep("sample_pickup", np.array([0.4, -0.1, 0.2, -2.0, 0.1, 1.6, 0.1]), 400),
        TaskStep("rack_place", np.array([0.3, 0.2, 0.1, -1.8, -0.1, 1.4, -0.1]), 300),
        TaskStep("reader_position", np.array([0.1, -0.4, 0.0, -1.3, 0.0, 1.0, 0.0]), 300),
    ]


def s4_inspection_trajectory() -> list[TaskStep]:
    """S4: Approach asset, contact inspection, retract."""
    return [
        TaskStep("home", np.array([0.0, -0.3, 0.0, -1.5, 0.0, 1.2, 0.0]), 300),
        TaskStep("approach_asset", np.array([0.4, 0.0, 0.3, -1.8, 0.0, 1.5, 0.0]), 400),
        TaskStep("contact_inspect", np.array([0.5, 0.1, 0.3, -2.0, 0.1, 1.7, 0.1]), 400),
        TaskStep("retract", np.array([0.0, -0.3, 0.0, -1.5, 0.0, 1.2, 0.0]), 300),
    ]


def s5_pharma_trajectory() -> list[TaskStep]:
    """S5: Pick vial from shelf, inspect, place or isolate."""
    return [
        TaskStep("home", np.array([0.0, -0.3, 0.0, -1.5, 0.0, 1.2, 0.0]), 300),
        TaskStep("shelf_approach", np.array([0.4, -0.2, 0.2, -1.9, 0.0, 1.5, 0.0]), 400),
        TaskStep("vial_pick", np.array([0.5, -0.1, 0.3, -2.0, 0.1, 1.7, 0.1]), 300),
        TaskStep("inspect_pose", np.array([0.2, -0.3, 0.1, -1.5, 0.0, 1.2, 0.0]), 300),
    ]


def s6_disassembly_trajectory() -> list[TaskStep]:
    """S6: Approach component, assess affordance, manipulate."""
    return [
        TaskStep("home", np.array([0.0, -0.3, 0.0, -1.5, 0.0, 1.2, 0.0]), 300),
        TaskStep("component_approach", np.array([0.3, 0.0, 0.2, -1.8, 0.0, 1.5, 0.0]), 400),
        TaskStep("assess_affordance", np.array([0.4, 0.1, 0.3, -2.0, 0.1, 1.7, 0.1]), 300),
        TaskStep("manipulate", np.array([0.5, 0.0, 0.2, -1.9, 0.0, 1.6, 0.2]), 300),
    ]
