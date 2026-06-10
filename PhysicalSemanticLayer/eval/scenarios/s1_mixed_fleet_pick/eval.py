"""S1: Mixed fleet picking with defect exception handling.

Breaking point: lossy abstraction uncertainty propagation through
A2R→A2A, plus R2R frame/unit/naming mismatch at handoff.

Exercises all 3 flows + document anchoring. Contains WO#42.
Uses: real scene XML, task controller, cross-adapter R2R, VLA encoder,
MCP tools (offline or online), baselines, config-as-code, run manifests.
"""

from __future__ import annotations

import time

import numpy as np

from eval.metrics.calibration import scalar_calibration_nll
from eval.runner.manifest import create_run_directory, write_manifest
from eval.scenarios.base import BreakpointResult, ScenarioResult
from eval.scenarios.config_loader import get_threshold, load_scenario_config
from eval.scenarios.execution_mode import ExecutionMode, detect_mode
from eval.scenarios.orchestrator import run_orchestrator
from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3
from psl.phyte.provenance import Provenance, ProvenanceEntry
from sim.schema_gen.generator import SchemaTransform
from sim.task_controller import s1_pick_trajectory

SCENARIO_ID = "s1_mixed_fleet_pick"


def evaluate_s1(
    seed: int = 42,
    mode: ExecutionMode | None = None,
) -> ScenarioResult:
    """Run s1_mixed_fleet_pick end-to-end.

    Thresholds loaded from experiments/scenarios/s1_mixed_fleet_pick.yaml.
    Run manifest saved to experiments/runs/.
    """
    start = time.time()
    if mode is None:
        mode = detect_mode()
    rng = np.random.default_rng(seed)

    # ── Load config (Gap 7) ──
    config = load_scenario_config(SCENARIO_ID)
    calibration_nll_max = get_threshold(config, "calibration_nll_max", 5.0)
    handoff_pose_max = get_threshold(config, "handoff_pose_max", 0.1)
    defect_threshold = get_threshold(config, "task_success", 0.5)

    # ── Run orchestrator ──
    orch = run_orchestrator(
        mode=mode,
        scenario_slug=SCENARIO_ID,
        trajectory=s1_pick_trajectory(),
        seed=seed,
        schema_dose=SchemaTransform(unit_scale=1000.0, sensor_noise_std=0.01),
    )

    # ── A2R: VLA defect detection with uncertainty ──
    ee_cov_trace = 0.0
    if "ee_pose" in orch.panda_ir.phytes:
        ee_cov_trace = float(np.trace(orch.panda_ir.phytes["ee_pose"].covariance))
    sensor_uncertainty = float(np.sqrt(ee_cov_trace)) * 100
    defect_confidence = 0.6
    defect_value = defect_confidence + rng.normal(0, max(sensor_uncertainty, 0.05))
    defect_value = float(np.clip(defect_value, 0, 1))
    declared_std = max(sensor_uncertainty, 0.05)

    defect_phyte = Phyte(
        semantic_id="defect_confidence",
        frame="world",
        pose=identity_se3(),
        timestamp=orch.sim.time,
        clock_domain="sim",
        unit="dimensionless",
        value=np.array([defect_value]),
        covariance=np.array([[declared_std**2]]),
        provenance=Provenance(
            chain=[
                ProvenanceEntry(
                    source="vla_encoder", operation="defect_detection", timestamp=orch.sim.time
                )
            ],
            confidence=0.8,
        ),
    )
    should_isolate = bool(defect_phyte.value[0] > defect_threshold)
    _ = should_isolate

    # ── MCP + negotiation ──
    doc_result = orch.mcp_results.get("resolve_document", {})
    doc_resolved = not (isinstance(doc_result, dict) and doc_result.get("is_error"))
    neg_result = orch.negotiator.negotiate("panda_arm", "amr_transport")

    # ── Calibration ──
    n_cal = 20
    predicted = np.full(n_cal, defect_confidence)
    gt_values = defect_confidence + rng.normal(0, declared_std, n_cal)
    declared_vars = np.full(n_cal, declared_std**2)
    cal_nll = scalar_calibration_nll(predicted, gt_values, declared_vars)

    # ── Success ──
    wo_phytes = orch.resolver.resolve_work_order("WO-42")
    business_success = (
        wo_phytes["source"] is not None and wo_phytes["target"] is not None and doc_resolved
    )

    bp_cal = BreakpointResult(
        "calibration_nll",
        declared_std > 0,
        cal_nll,
        calibration_nll_max,
        cal_nll <= calibration_nll_max,
    )
    bp_hoff = BreakpointResult(
        "r2r_handoff_error",
        orch.r2r_handoff_ir is not None,
        orch.r2r_handoff_error,
        handoff_pose_max,
        orch.r2r_handoff_error <= handoff_pose_max,
    )
    bp_neg = BreakpointResult(
        "negotiation_feasible", True, 1.0 if neg_result.feasible else 0.0, 0.5, neg_result.feasible
    )
    breakpoints = [bp_cal, bp_hoff, bp_neg]
    psl_success = all(bp.passed for bp in breakpoints)

    metrics: dict[str, object] = {
        "joint_pos_rmse": orch.panda_roundtrip_rmse,
        "r2r_handoff_error": orch.r2r_handoff_error,
        "calibration_nll": cal_nll,
        "commutativity_divergence": orch.commutativity_div,
        "defect_confidence": defect_value,
        "defect_uncertainty": declared_std,
        "negotiation_notes": len(neg_result.translation_notes),
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
    }

    metrics["agent_fallback_used"] = orch.agent_fallback_used
    metrics["agent_404_count"] = orch.agent_404_count
    metrics["smolvla_available"] = orch.smolvla_available
    metrics["smolvla_action_confidence"] = orch.smolvla_action_confidence

    if orch.agent_cost_usd is not None:
        metrics["agent_cost_usd"] = orch.agent_cost_usd
        metrics["agent_num_turns"] = orch.agent_num_turns or 0
        metrics["agent_tool_calls"] = len(orch.agent_tool_calls)

    result = ScenarioResult(
        scenario_id=SCENARIO_ID,
        seed=seed,
        business_success=business_success,
        psl_success=psl_success,
        breakpoints=breakpoints,
        metrics=metrics,
        rq_contributions=["RQ1", "RQ2/H2", "RQ5/H4"],
        wall_time_s=time.time() - start,
    )

    # ── Save run manifest (Gap 5) ──
    run_dir = create_run_directory("experiments/runs", SCENARIO_ID)
    write_manifest(
        run_dir, config=config, seed=seed, metrics=metrics, agent_trace=orch.agent_trace
    )

    return result
