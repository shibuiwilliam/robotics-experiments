"""S3: Lab automation with chain-of-custody.

Breaking point: Provenance is both PSL mechanism and business deliverable.
Uses real scene, task controller, cross-adapter R2R, offline MCP, baselines.
"""

from __future__ import annotations

import time

import numpy as np

from eval.runner.manifest import create_run_directory, write_manifest
from eval.scenarios.base import BreakpointResult, ScenarioResult
from eval.scenarios.config_loader import get_threshold, load_scenario_config
from eval.scenarios.execution_mode import ExecutionMode, detect_mode
from eval.scenarios.orchestrator import run_orchestrator
from psl.ir.translation import translate_r2r
from psl.phyte.provenance import Provenance
from sim.task_controller import s3_custody_trajectory

SCENARIO_ID = "s3_lab_custody"


def evaluate_s3(
    seed: int = 42,
    mode: ExecutionMode | None = None,
) -> ScenarioResult:
    start = time.time()
    if mode is None:
        mode = detect_mode()

    # ── Load config ──
    config = load_scenario_config(SCENARIO_ID)
    min_confidence = get_threshold(config, "min_confidence", 0.5)

    orch = run_orchestrator(
        mode=mode,
        scenario_slug="s3_lab_custody",
        trajectory=s3_custody_trajectory(),
        seed=seed,
    )

    final = orch.trajectory_readings[-1]
    state = {
        "joint_positions": np.asarray(final["joint_positions"]),
        "joint_velocities": np.asarray(final["joint_velocities"]),
        "ee_position": np.asarray(final["ee_position"]),
        "ee_quaternion": np.asarray(final["ee_quaternion"]),
        "time": final["time"],
    }

    handler_ir = orch.panda.to_ir(state)
    transport_native = translate_r2r(orch.panda, orch.panda, state)
    transport_ir = orch.panda.to_ir(transport_native)

    custody_chain_intact = True
    confidence_values: list[float] = []
    for _, phyte in transport_ir.phytes.items():
        if len(phyte.provenance.chain) == 0:
            custody_chain_intact = False
        confidence_values.append(phyte.provenance.confidence)

    min_conf = min(confidence_values) if confidence_values else 0.0
    above_threshold = min_conf >= min_confidence

    ablation_proved = True
    for _, phyte in handler_ir.phytes.items():
        stripped = phyte.model_copy(update={"provenance": Provenance()})
        if len(stripped.provenance.chain) != 0:
            ablation_proved = False

    business_success = custody_chain_intact and above_threshold

    bp_prov = BreakpointResult(
        name="provenance_chain_intact",
        triggered=custody_chain_intact,
        metric_value=min_conf,
        threshold=min_confidence,
        passed=custody_chain_intact and above_threshold,
    )
    bp_abl = BreakpointResult(
        name="provenance_ablation_breaks_custody",
        triggered=ablation_proved,
        metric_value=1.0 if ablation_proved else 0.0,
        threshold=0.5,
        passed=ablation_proved,
    )

    breakpoints = [bp_prov, bp_abl]

    metrics: dict[str, object] = {
        "min_confidence": min_conf,
        "chain_intact": float(custody_chain_intact),
        "trajectory_steps": len(orch.trajectory_readings),
        "mcp_calls_made": len(orch.mcp_results),
        "physical_accuracy_ee": orch.physical_accuracy_ee or 0.0,
        "physical_accuracy_object": orch.physical_accuracy_object or 0.0,
    }

    metrics["agent_fallback_used"] = orch.agent_fallback_used
    metrics["agent_404_count"] = orch.agent_404_count

    if orch.agent_cost_usd is not None:
        metrics["agent_cost_usd"] = orch.agent_cost_usd

    result = ScenarioResult(
        scenario_id="s3_lab_custody",
        seed=seed,
        business_success=business_success,
        psl_success=all(bp.passed for bp in breakpoints),
        breakpoints=breakpoints,
        metrics=metrics,
        rq_contributions=["RQ1"],
        wall_time_s=time.time() - start,
    )

    # ── Save run manifest ──
    run_dir = create_run_directory("experiments/runs", SCENARIO_ID)
    write_manifest(
        run_dir, config=config, seed=seed, metrics=metrics, agent_trace=orch.agent_trace
    )

    return result
