"""S7: Degraded operations under clock skew and communication delay.

Breaking point: Clock skew → causal ordering preservation,
graceful degradation. Uses real scene (reuses s1), task controller,
cross-adapter R2R, offline MCP, baselines.
"""

from __future__ import annotations

import time

import numpy as np

from eval.metrics.se3 import joint_rmse
from eval.runner.manifest import create_run_directory, write_manifest
from eval.scenarios.base import BreakpointResult, ScenarioResult
from eval.scenarios.config_loader import load_scenario_config
from eval.scenarios.execution_mode import ExecutionMode, detect_mode
from eval.scenarios.orchestrator import run_orchestrator
from psl.adapters.robots.panda.adapter import PandaAdapter
from psl.lod.resolution import LODSubscriber
from sim.task_controller import s1_pick_trajectory

SCENARIO_ID = "s7_degraded_ops"


def evaluate_s7(
    seed: int = 42,
    skew_values: list[float] | None = None,
    mode: ExecutionMode | None = None,
) -> ScenarioResult:
    start = time.time()
    if mode is None:
        mode = detect_mode()
    if skew_values is None:
        skew_values = [0.0, 0.001, 0.01, 0.1, 0.5, 1.0]

    # ── Load config ──
    config = load_scenario_config(SCENARIO_ID)

    orch = run_orchestrator(
        mode=mode,
        scenario_slug="s7_degraded_ops",
        trajectory=s1_pick_trajectory(),
        seed=seed,
    )

    adapter = PandaAdapter(entity_id="panda_0")
    final = orch.trajectory_readings[-1]
    base_state: dict[str, object] = {
        "joint_positions": np.asarray(final["joint_positions"]),
        "joint_velocities": np.asarray(final["joint_velocities"]),
        "ee_position": np.asarray(final["ee_position"]),
        "ee_quaternion": np.asarray(final["ee_quaternion"]),
        "time": final["time"],
    }
    ir_base = adapter.to_ir(base_state)
    jpos = np.asarray(base_state["joint_positions"])

    causal_violations = 0
    fidelity_by_skew: list[tuple[float, float]] = []
    degradation_monotonic = True
    prev_fidelity = 0.0

    for skew in skew_values:
        skewed = {**base_state, "time": float(base_state["time"]) + skew}  # type: ignore[arg-type]
        ir_skewed = adapter.to_ir(skewed)
        rt = adapter.from_ir(ir_skewed)
        fidelity = joint_rmse(np.asarray(rt["joint_positions"]), jpos)
        fidelity_by_skew.append((skew, fidelity))
        for name, phyte in ir_skewed.phytes.items():
            if (
                name in ir_base.phytes
                and skew > 0
                and phyte.timestamp < ir_base.phytes[name].timestamp
            ):
                causal_violations += 1
        if fidelity < prev_fidelity - 1e-12:
            degradation_monotonic = False
        prev_fidelity = fidelity

    lod = LODSubscriber(current_time_fn=lambda: orch.sim.time + 2.0)
    summary = lod.to_summary("panda_0", ir_base.phytes)

    bp_causal = BreakpointResult(
        name="causal_ordering_violations",
        triggered=True,
        metric_value=float(causal_violations),
        threshold=0.5,
        passed=causal_violations == 0,
    )
    bp_degrade = BreakpointResult(
        name="graceful_degradation",
        triggered=True,
        metric_value=1.0 if degradation_monotonic else 0.0,
        threshold=0.5,
        passed=degradation_monotonic,
    )

    breakpoints = [bp_causal, bp_degrade]

    metrics: dict[str, object] = {
        "causal_violations": float(causal_violations),
        "degradation_monotonic": float(degradation_monotonic),
        "fidelity_by_skew": {str(s): f for s, f in fidelity_by_skew},
        "trajectory_steps": len(orch.trajectory_readings),
        "mcp_calls_made": len(orch.mcp_results),
        "lod_staleness": summary.staleness_s,
        "physical_accuracy_ee": orch.physical_accuracy_ee or 0.0,
        "physical_accuracy_object": orch.physical_accuracy_object or 0.0,
    }

    metrics["agent_fallback_used"] = orch.agent_fallback_used
    metrics["agent_404_count"] = orch.agent_404_count

    if orch.agent_cost_usd is not None:
        metrics["agent_cost_usd"] = orch.agent_cost_usd

    result = ScenarioResult(
        scenario_id="s7_degraded_ops",
        seed=seed,
        business_success=True,
        psl_success=all(bp.passed for bp in breakpoints),
        breakpoints=breakpoints,
        metrics=metrics,
        rq_contributions=["RQ_causal", "Phase4"],
        wall_time_s=time.time() - start,
    )

    # ── Save run manifest ──
    run_dir = create_run_directory("experiments/runs", SCENARIO_ID)
    write_manifest(
        run_dir, config=config, seed=seed, metrics=metrics, agent_trace=orch.agent_trace
    )

    return result
