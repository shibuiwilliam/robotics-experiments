"""S4: Field asset inspection with multi-resolution fusion.

Breaking point: Multi-resolution stream fusion + bidirectional anchoring.
Uses real scene, task controller, cross-adapter R2R, offline MCP, baselines.
"""

from __future__ import annotations

import time

from eval.runner.manifest import create_run_directory, write_manifest
from eval.scenarios.base import BreakpointResult, ScenarioResult
from eval.scenarios.config_loader import get_threshold, load_scenario_config
from eval.scenarios.execution_mode import ExecutionMode, detect_mode
from eval.scenarios.orchestrator import run_orchestrator
from psl.lod.resolution import LODSubscriber
from sim.task_controller import s4_inspection_trajectory

SCENARIO_ID = "s4_field_inspection"


def evaluate_s4(
    seed: int = 42,
    mode: ExecutionMode | None = None,
) -> ScenarioResult:
    start = time.time()
    if mode is None:
        mode = detect_mode()

    # ── Load config ──
    config = load_scenario_config(SCENARIO_ID)
    fidelity_max = get_threshold(config, "fidelity_max", 0.05)

    orch = run_orchestrator(
        mode=mode,
        scenario_slug="s4_field_inspection",
        trajectory=s4_inspection_trajectory(),
        seed=seed,
    )

    lod = LODSubscriber(current_time_fn=lambda: orch.sim.time)
    raw = lod.to_raw(orch.panda_ir.phytes)
    summary = lod.to_summary("contact_arm", orch.panda_ir.phytes)
    semantic = lod.to_semantic("contact_arm", orch.panda_ir.phytes)
    lod_correct = summary.n_joints > 0 and len(semantic.description) > 0

    doc_result = orch.mcp_results.get("resolve_document", {})
    anchoring_works = isinstance(doc_result, dict) and not doc_result.get("is_error", False)
    rt_error = orch.panda_roundtrip_rmse

    business_success = lod_correct and anchoring_works

    bp_lod = BreakpointResult(
        name="lod_consistency",
        triggered=True,
        metric_value=float(len(raw)),
        threshold=1.0,
        passed=lod_correct,
    )
    bp_fid = BreakpointResult(
        name="fusion_fidelity",
        triggered=True,
        metric_value=rt_error,
        threshold=fidelity_max,
        passed=rt_error <= fidelity_max,
    )
    bp_anch = BreakpointResult(
        name="bidirectional_anchoring",
        triggered=anchoring_works,
        metric_value=1.0 if anchoring_works else 0.0,
        threshold=0.5,
        passed=anchoring_works,
    )

    # ── Drone aerial inspection phase (N+N scaling proof) ──
    import numpy as np

    from psl.adapters.robots.drone.adapter import DroneAdapter
    from psl.negotiation.handshake import CapabilityDescriptor, ControlMode

    drone = DroneAdapter(entity_id="inspection_drone")
    orch.world_model.register_entity("inspection_drone", parent_id="world")
    orch.negotiator.register(
        CapabilityDescriptor(
            entity_id="inspection_drone",
            entity_type="robot",
            n_joints=0,
            control_modes=(ControlMode.VELOCITY,),
            frame_convention="enu",
            unit_system="SI",
            semantic_capabilities=("fly", "inspect"),
        )
    )

    # Simulate drone observing the workspace from above
    drone_native: dict[str, object] = {
        "position_enu": np.array([0.0, 0.5, 2.0]),
        "orientation_quat_hamilton": np.array([1.0, 0.0, 0.0, 0.0]),
        "linear_velocity_enu": np.zeros(3),
        "angular_velocity_body": np.zeros(3),
        "time": orch.sim.time,
    }
    drone_ir = drone.to_ir(drone_native)
    orch.world_model.write("inspection_drone", drone_ir.phytes)

    # Cross-adapter R2R: Drone → IR → round-trip
    drone_roundtrip = drone.from_ir(drone_ir)
    drone_rt_error = float(
        np.linalg.norm(
            np.asarray(drone_native["position_enu"]) - np.asarray(drone_roundtrip["position_enu"])
        )
    )

    # Negotiation: drone ↔ panda
    neg_result = orch.negotiator.negotiate("inspection_drone", "panda_arm")

    bp_drone = BreakpointResult(
        name="drone_r2r_handoff",
        triggered=True,
        metric_value=drone_rt_error,
        threshold=1e-6,
        passed=drone_rt_error <= 1e-6,
    )

    breakpoints = [bp_lod, bp_fid, bp_anch, bp_drone]

    metrics: dict[str, object] = {
        "rt_error": rt_error,
        "lod_joints": summary.n_joints,
        "trajectory_steps": len(orch.trajectory_readings),
        "mcp_calls_made": len(orch.mcp_results),
        "baseline_psl_rmse": orch.baseline_results.get("PSL", {}).get("joint_rmse", -1),
        "baseline_b0_rmse": orch.baseline_results.get("B0", {}).get("joint_rmse", -1),
        "baseline_b0_info_loss": orch.baseline_results.get("B0", {}).get("info_loss", -1),
        "baseline_b1_rmse": orch.baseline_results.get("B1", {}).get("joint_rmse", -1),
        "baseline_b2_rmse": orch.baseline_results.get("B2", {}).get("joint_rmse", -1),
        "baseline_b2_info_loss": orch.baseline_results.get("B2", {}).get("info_loss", -1),
        "physical_accuracy_ee": orch.physical_accuracy_ee or 0.0,
        "physical_accuracy_object": orch.physical_accuracy_object or 0.0,
        "drone_rt_error": drone_rt_error,
        "drone_negotiation_feasible": neg_result.feasible,
        "drone_negotiation_notes": len(neg_result.translation_notes),
        "agent_fallback_used": orch.agent_fallback_used,
        "agent_404_count": orch.agent_404_count,
        "smolvla_available": orch.smolvla_available,
        "smolvla_action_confidence": orch.smolvla_action_confidence,
    }

    if orch.agent_cost_usd is not None:
        metrics["agent_cost_usd"] = orch.agent_cost_usd

    result = ScenarioResult(
        scenario_id="s4_field_inspection",
        seed=seed,
        business_success=business_success,
        psl_success=all(bp.passed for bp in breakpoints),
        breakpoints=breakpoints,
        metrics=metrics,
        rq_contributions=["RQ1", "RQ5/H4"],
        wall_time_s=time.time() - start,
    )

    # ── Save run manifest ──
    run_dir = create_run_directory("experiments/runs", SCENARIO_ID)
    write_manifest(run_dir, config=config, seed=seed, metrics=metrics)
    if orch.agent_trace:
        import json as _json

        (run_dir / "agent_trace.json").write_text(
            _json.dumps(orch.agent_trace, indent=2, default=str)
        )

    return result
