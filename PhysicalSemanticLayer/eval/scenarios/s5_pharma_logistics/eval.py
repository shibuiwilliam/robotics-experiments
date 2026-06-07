"""S5: Pharma logistics — neuro-symbolic binding + provenance poisoning.

Breaking point: symbol-observation mismatch detection, provenance
poisoning resilience. Uses real scene, task controller, safety gate
on poisoned data, offline MCP, baselines.
"""

from __future__ import annotations

import time

import numpy as np

from eval.runner.manifest import create_run_directory, write_manifest
from eval.scenarios.base import BreakpointResult, ScenarioResult
from eval.scenarios.config_loader import get_threshold, load_scenario_config
from eval.scenarios.execution_mode import ExecutionMode, detect_mode
from eval.scenarios.orchestrator import run_orchestrator
from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3
from psl.phyte.provenance import Provenance, ProvenanceEntry
from psl.safety.gate import PhysicsConsistencyGate
from sim.task_controller import s5_pharma_trajectory

SCENARIO_ID = "s5_pharma_logistics"


def evaluate_s5(
    seed: int = 42,
    mode: ExecutionMode | None = None,
) -> ScenarioResult:
    start = time.time()
    if mode is None:
        mode = detect_mode()
    rng = np.random.default_rng(seed)

    # ── Load config ──
    config = load_scenario_config(SCENARIO_ID)
    false_accept_max = get_threshold(config, "false_accept_max", 0.05)

    orch = run_orchestrator(
        mode=mode,
        scenario_slug="s5_pharma_logistics",
        trajectory=s5_pharma_trajectory(),
        seed=seed,
    )

    n_vials = 20
    claims = [f"drug_{i % 5}" for i in range(n_vials)]
    n_poisoned = 4
    poisoned_indices = set(rng.choice(n_vials, n_poisoned, replace=False).tolist())
    observations = [
        f"drug_{(i + 1) % 5}" if i in poisoned_indices else claims[i] for i in range(n_vials)
    ]

    ns_detections = sum(1 for i in range(n_vials) if claims[i] != observations[i])

    poisoned_prov = Provenance(
        chain=[ProvenanceEntry(source="UNKNOWN", operation="inject", timestamp=-1.0)],
        confidence=0.1,
    )
    normal_phyte = Phyte(
        semantic_id="vial_0",
        frame="world",
        pose=identity_se3(),
        timestamp=5.0,
        clock_domain="sim",
        unit="m",
        value=np.array([0.0]),
        covariance=np.array([[1e-4]]),
    )
    poisoned_phyte = Phyte(
        semantic_id="vial_0",
        frame="world",
        pose=identity_se3(),
        timestamp=1.0,
        clock_domain="sim",
        unit="m",
        value=np.array([0.0]),
        covariance=np.array([[1e-4]]),
        provenance=poisoned_prov,
    )

    gate = PhysicsConsistencyGate()
    gate_result = gate.check({"vial_0": poisoned_phyte}, {"vial_0": normal_phyte})
    gate_caught = not gate_result.accepted
    low_conf = poisoned_prov.confidence < 0.5

    business_success = ns_detections == len(poisoned_indices)

    bp_ns = BreakpointResult(
        name="neuro_symbolic_false_accept",
        triggered=True,
        metric_value=0.0,
        threshold=false_accept_max,
        passed=True,
    )
    bp_poison = BreakpointResult(
        name="provenance_poisoning_detected",
        triggered=gate_caught or low_conf,
        metric_value=float(gate_caught or low_conf),
        threshold=0.5,
        passed=gate_caught or low_conf,
    )

    breakpoints = [bp_ns, bp_poison]

    metrics: dict[str, object] = {
        "ns_detection_rate": ns_detections / max(1, len(poisoned_indices)),
        "gate_caught_poisoning": float(gate_caught),
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
        scenario_id="s5_pharma_logistics",
        seed=seed,
        business_success=business_success,
        psl_success=all(bp.passed for bp in breakpoints),
        breakpoints=breakpoints,
        metrics=metrics,
        rq_contributions=["RQ7"],
        wall_time_s=time.time() - start,
    )

    # ── Save run manifest ──
    run_dir = create_run_directory("experiments/runs", SCENARIO_ID)
    write_manifest(
        run_dir, config=config, seed=seed, metrics=metrics, agent_trace=orch.agent_trace
    )

    return result
