"""S6: Open-world e-waste disassembly with affordance grounding.

Breaking point: Novel objects not in ontology — CLIP-based semantic
embedding generalizes to unseen object names, symbol-only lookup fails.

H5 validation: Uses REAL VLAEncoder.predict_affordances() calls,
not synthetic simulation. Compares actual CLIP zero-shot predictions
against ground-truth affordances for holdout objects.
"""

from __future__ import annotations

import time

import numpy as np

from eval.runner.manifest import create_run_directory, write_manifest
from eval.scenarios.base import BreakpointResult, ScenarioResult
from eval.scenarios.config_loader import get_threshold, load_scenario_config
from eval.scenarios.execution_mode import ExecutionMode, detect_mode
from eval.scenarios.orchestrator import run_orchestrator
from psl.grounding.vla_encoder import VLAEncoder
from sim.task_controller import s6_disassembly_trajectory

SCENARIO_ID = "s6_ewaste_disassembly"

# Ground-truth affordances for known objects (in training ontology)
KNOWN_OBJECTS = {
    "circuit_board": {"graspable": True, "detachable": True, "material": "pcb"},
    "battery_pack": {"graspable": True, "detachable": True, "material": "lithium"},
    "plastic_case": {"graspable": True, "detachable": False, "material": "abs"},
    "copper_wire": {"graspable": False, "detachable": True, "material": "copper"},
}

# Ground-truth affordances for holdout objects (NOT in training ontology)
HOLDOUT_OBJECTS = {
    "capacitor": {"graspable": True, "detachable": True, "material": "aluminum"},
    "heat_sink": {"graspable": True, "detachable": True, "material": "aluminum"},
    "ribbon_cable": {"graspable": False, "detachable": True, "material": "copper"},
}


def _symbol_only_predict(obj_name: str) -> dict[str, bool]:
    """Symbol-only prediction: lookup in known dict, False for unknowns."""
    if obj_name in KNOWN_OBJECTS:
        return {k: v for k, v in KNOWN_OBJECTS[obj_name].items() if isinstance(v, bool)}
    return {"graspable": False, "detachable": False}


def _vla_predict(encoder: VLAEncoder, obj_name: str) -> dict[str, object]:
    """Call real VLA encoder to predict affordances for an object.

    Uses CLIP zero-shot classification (or hash fallback) to predict
    graspable, detachable, and material from the object name's
    semantic embedding.
    """
    pred = encoder.predict_affordances(obj_name)
    return {
        "graspable": pred.graspable,
        "detachable": pred.detachable,
        "material": pred.material,
    }


def evaluate_s6(
    seed: int = 42,
    mode: ExecutionMode | None = None,
) -> ScenarioResult:
    start = time.time()
    if mode is None:
        mode = detect_mode()

    # ── Load config ──
    config = load_scenario_config(SCENARIO_ID)
    embedding_advantage_min = get_threshold(config, "embedding_advantage_min", 0.1)

    orch = run_orchestrator(
        mode=mode,
        scenario_slug="s6_ewaste_disassembly",
        trajectory=s6_disassembly_trajectory(),
        seed=seed,
    )

    # ── H5 validation: real VLA predictions vs symbol-only ──
    # Create a VLA encoder matching the orchestrator's configuration
    encoder = VLAEncoder(use_real_clip=True, seed=seed)
    vla_mode = "clip" if encoder._use_real_clip else "hash_fallback"

    holdout_names = list(HOLDOUT_OBJECTS.keys())

    # Track both per-object (all attributes match) and per-attribute accuracy
    sym_obj_correct = 0
    vla_obj_correct = 0
    sym_attr_correct = 0
    vla_attr_correct = 0
    total_objects = 0
    total_attrs = 0

    per_object: list[dict[str, object]] = []

    for obj_name in holdout_names:
        gt = HOLDOUT_OBJECTS[obj_name]
        gt_bool = {k: v for k, v in gt.items() if isinstance(v, bool)}

        # Symbol-only: lookup in known dict (returns False for unknowns)
        sym_pred = _symbol_only_predict(obj_name)
        sym_all_ok = all(sym_pred.get(k) == v for k, v in gt_bool.items())
        sym_obj_correct += int(sym_all_ok)
        sym_attr_hits = sum(1 for k, v in gt_bool.items() if sym_pred.get(k) == v)

        # VLA: actual predict_affordances() call
        vla_pred = _vla_predict(encoder, obj_name)
        vla_all_ok = all(vla_pred.get(k) == v for k, v in gt_bool.items())
        vla_obj_correct += int(vla_all_ok)
        vla_attr_hits = sum(1 for k, v in gt_bool.items() if vla_pred.get(k) == v)

        n_attrs = len(gt_bool)
        sym_attr_correct += sym_attr_hits
        vla_attr_correct += vla_attr_hits
        total_objects += 1
        total_attrs += n_attrs

        per_object.append(
            {
                "object": obj_name,
                "ground_truth": gt_bool,
                "symbol_pred": sym_pred,
                "symbol_attr_accuracy": sym_attr_hits / n_attrs if n_attrs else 0.0,
                "vla_pred": {k: v for k, v in vla_pred.items() if isinstance(v, bool)},
                "vla_attr_accuracy": vla_attr_hits / n_attrs if n_attrs else 0.0,
            }
        )

    # Per-object exact-match rate (strict)
    sym_obj_rate = sym_obj_correct / total_objects if total_objects else 0.0
    vla_obj_rate = vla_obj_correct / total_objects if total_objects else 0.0

    # Per-attribute accuracy (lenient — partial credit)
    sym_attr_rate = sym_attr_correct / total_attrs if total_attrs else 0.0
    vla_attr_rate = vla_attr_correct / total_attrs if total_attrs else 0.0

    # Primary metric: per-attribute advantage (more informative than all-or-nothing)
    advantage = vla_attr_rate - sym_attr_rate

    # Business success: VLA does better than symbol-only on at least one metric
    business_success = vla_attr_rate > sym_attr_rate

    bp_gen = BreakpointResult(
        name="embedding_generalization_advantage",
        triggered=True,
        metric_value=advantage,
        threshold=embedding_advantage_min,
        passed=advantage >= embedding_advantage_min,
    )

    breakpoints = [bp_gen]

    # ── Action-level evaluation (when SmolVLA is available) ──
    has_action_model = False
    action_validity_rate = 0.0
    action_results: list[dict[str, object]] = []

    try:
        from psl.grounding.smolvla_encoder import SmolVLAEncoder

        action_encoder = SmolVLAEncoder(use_smolvla=True, seed=seed)
        has_action_model = action_encoder.has_action_model

        if has_action_model:
            for obj_name in holdout_names:
                instruction = f"pick up the {obj_name}"
                image = orch.sim.render(256, 256)  # real MuJoCo scene
                robot_state: dict[str, object] = {
                    "joint_positions": np.zeros(7),
                }
                action = action_encoder.predict_action(image, instruction, robot_state)
                # Validate that action can be wrapped as Phyte (provenance + covariance)
                _ = action_encoder.action_to_phyte(action, timestamp=orch.sim.time)

                action_results.append(
                    {
                        "object": obj_name,
                        "action_confidence": action.confidence,
                        "gripper": action.gripper,
                        "ee_delta_norm": float(np.linalg.norm(action.ee_delta)),
                        "action_valid": action.confidence > 0,
                    }
                )

            valid_actions = sum(1 for r in action_results if r["action_valid"])
            action_validity_rate = valid_actions / len(action_results) if action_results else 0.0
    except ImportError:
        pass  # SmolVLA not available — skip action evaluation

    if has_action_model:
        bp_action = BreakpointResult(
            name="vla_action_validity",
            triggered=True,
            metric_value=action_validity_rate,
            threshold=0.5,
            passed=action_validity_rate >= 0.5,
        )
        breakpoints.append(bp_action)

    metrics: dict[str, object] = {
        "symbol_only_rate": sym_attr_rate,
        "embedding_rate": vla_attr_rate,
        "advantage": advantage,
        "symbol_obj_exact_match": sym_obj_rate,
        "vla_obj_exact_match": vla_obj_rate,
        "vla_mode": vla_mode,
        "n_holdout_objects": total_objects,
        "per_object_results": per_object,
        "has_action_model": has_action_model,
        "action_validity_rate": action_validity_rate,
        "action_results": action_results,
        "trajectory_steps": len(orch.trajectory_readings),
        "mcp_calls_made": len(orch.mcp_results),
        "physical_accuracy_ee": orch.physical_accuracy_ee or 0.0,
        "physical_accuracy_object": orch.physical_accuracy_object or 0.0,
        "baseline_b0_rmse": orch.baseline_results.get("B0", {}).get("joint_rmse", -1),
        "baseline_b0_info_loss": orch.baseline_results.get("B0", {}).get("info_loss", -1),
        "baseline_b2_rmse": orch.baseline_results.get("B2", {}).get("joint_rmse", -1),
        "baseline_b2_info_loss": orch.baseline_results.get("B2", {}).get("info_loss", -1),
        "agent_fallback_used": orch.agent_fallback_used,
        "agent_404_count": orch.agent_404_count,
        "smolvla_available": orch.smolvla_available,
        "smolvla_action_confidence": orch.smolvla_action_confidence,
    }

    if orch.agent_cost_usd is not None:
        metrics["agent_cost_usd"] = orch.agent_cost_usd

    result = ScenarioResult(
        scenario_id="s6_ewaste_disassembly",
        seed=seed,
        business_success=business_success,
        psl_success=all(bp.passed for bp in breakpoints),
        breakpoints=breakpoints,
        metrics=metrics,
        rq_contributions=["RQ6/H5"],
        wall_time_s=time.time() - start,
    )

    # ── Save run manifest ──
    run_dir = create_run_directory("experiments/runs", SCENARIO_ID)
    write_manifest(
        run_dir, config=config, seed=seed, metrics=metrics, agent_trace=orch.agent_trace
    )

    return result
