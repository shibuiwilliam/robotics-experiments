"""S2: Manufacturing line changeover (recipe switching).

Breaking point: (a) impossible recipe → safety gate rejection,
(b) multi-hop commutativity. Uses real scene, task controller,
cross-adapter R2R, offline MCP, baselines.
"""

from __future__ import annotations

import time

import numpy as np

from eval.metamorphic.compositionality import commutativity_divergence
from eval.runner.manifest import create_run_directory, write_manifest
from eval.scenarios.base import BreakpointResult, ScenarioResult
from eval.scenarios.config_loader import get_threshold, load_scenario_config
from eval.scenarios.execution_mode import ExecutionMode, detect_mode
from eval.scenarios.orchestrator import run_orchestrator
from psl.adapters.robots.panda.heterogeneous import HeterogeneousPandaAdapter
from psl.safety.gate import JointLimits, PhysicsConsistencyGate
from sim.schema_gen.generator import SchemaTransform
from sim.task_controller import s2_changeover_trajectory

SCENARIO_ID = "s2_line_changeover"


def evaluate_s2(
    seed: int = 42,
    mode: ExecutionMode | None = None,
) -> ScenarioResult:
    start = time.time()
    if mode is None:
        mode = detect_mode()

    # ── Load config ──
    config = load_scenario_config(SCENARIO_ID)
    commutativity_max = get_threshold(config, "commutativity_max", 1e-6)

    orch = run_orchestrator(
        mode=mode,
        scenario_slug="s2_line_changeover",
        trajectory=s2_changeover_trajectory(),
        seed=seed,
        schema_dose=SchemaTransform(unit_scale=1000.0, frame_rotation_z_rad=np.pi / 6),
        sensor_prefix="a_",  # Dual-panda scene uses a_/b_ prefixed sensors
    )

    transform_b = SchemaTransform(unit_scale=1000.0, frame_rotation_z_rad=np.pi / 6)
    adapter_b = HeterogeneousPandaAdapter(entity_id="robot_b", transform=transform_b)

    final = orch.trajectory_readings[-1]
    state_a = {
        "joint_positions": np.asarray(final["joint_positions"]),
        "joint_velocities": np.asarray(final["joint_velocities"]),
        "ee_position": np.asarray(final["ee_position"]),
        "ee_quaternion": np.asarray(final["ee_quaternion"]),
        "time": final["time"],
    }
    comm_div = commutativity_divergence(orch.panda, adapter_b, state_a)

    lower, upper = orch.sim.get_joint_limits("a_")
    gate = PhysicsConsistencyGate(
        joint_limits=JointLimits(
            position_lower=lower,
            position_upper=upper,
            velocity_max=np.full(len(lower), 2.61),
        )
    )
    bad_state = dict(state_a)
    bad_jpos = np.asarray(state_a["joint_positions"]).copy()
    bad_jpos[0] = float(upper[0]) + 0.5
    bad_state["joint_positions"] = bad_jpos
    impossible_ir = orch.panda.to_ir(bad_state)
    gate_result = gate.check(impossible_ir.phytes)
    gate_rejected = not gate_result.accepted

    valid_ir = orch.panda.to_ir(state_a)
    false_reject = not gate.check(valid_ir.phytes).accepted

    business_success = gate_rejected and not false_reject

    bp_comm = BreakpointResult(
        name="commutativity_divergence",
        triggered=True,
        metric_value=comm_div,
        threshold=commutativity_max,
        passed=comm_div <= commutativity_max,
    )
    bp_safety = BreakpointResult(
        name="safety_gate_rejection",
        triggered=gate_rejected,
        metric_value=0.0 if gate_rejected else 1.0,
        threshold=0.5,
        passed=gate_rejected,
    )

    breakpoints = [bp_comm, bp_safety]

    metrics: dict[str, object] = {
        "commutativity_divergence": comm_div,
        "gate_rejected_impossible": float(gate_rejected),
        "false_reject": float(false_reject),
        "trajectory_steps": len(orch.trajectory_readings),
        "baseline_psl_rmse": orch.baseline_results.get("PSL", {}).get("joint_rmse", -1),
        "physical_accuracy_ee": orch.physical_accuracy_ee or 0.0,
        "physical_accuracy_object": orch.physical_accuracy_object or 0.0,
    }

    metrics["agent_fallback_used"] = orch.agent_fallback_used
    metrics["agent_404_count"] = orch.agent_404_count

    if orch.agent_cost_usd is not None:
        metrics["agent_cost_usd"] = orch.agent_cost_usd

    result = ScenarioResult(
        scenario_id="s2_line_changeover",
        seed=seed,
        business_success=business_success,
        psl_success=all(bp.passed for bp in breakpoints),
        breakpoints=breakpoints,
        metrics=metrics,
        rq_contributions=["RQ3/H3", "RQ7"],
        wall_time_s=time.time() - start,
    )

    # ── Save run manifest ──
    run_dir = create_run_directory("experiments/runs", SCENARIO_ID)
    write_manifest(
        run_dir, config=config, seed=seed, metrics=metrics, agent_trace=orch.agent_trace
    )

    return result
