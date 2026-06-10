"""Tests for all 7 verification scenarios.

Each test runs the scenario eval and checks:
  1. Business success (scenario-specific goal met)
  2. PSL success (breakpoint metrics meet thresholds)
  3. Breakpoints were actually triggered
  4. Two-layer pass (both must hold)
"""

from __future__ import annotations

import pytest

from eval.scenarios.s1_mixed_fleet_pick.eval import evaluate_s1
from eval.scenarios.s2_line_changeover.eval import evaluate_s2
from eval.scenarios.s3_lab_custody.eval import evaluate_s3
from eval.scenarios.s4_field_inspection.eval import evaluate_s4
from eval.scenarios.s5_pharma_logistics.eval import evaluate_s5
from eval.scenarios.s6_ewaste_disassembly.eval import evaluate_s6
from eval.scenarios.s7_degraded_ops.eval import evaluate_s7

SEED = 42


@pytest.mark.oracle
class TestS1MixedFleetPick:
    """S1: Mixed fleet picking with defect exception handling."""

    def test_two_layer_success(self) -> None:
        result = evaluate_s1(seed=SEED)
        assert result.business_success, "S1 business goal failed"
        assert result.psl_success, (
            f"S1 PSL failed: {[bp for bp in result.breakpoints if not bp.passed]}"
        )
        assert result.overall_pass

    def test_breakpoints_triggered(self) -> None:
        result = evaluate_s1(seed=SEED)
        for bp in result.breakpoints:
            assert bp.triggered, f"S1 breakpoint '{bp.name}' was not triggered"

    def test_rq_contributions(self) -> None:
        result = evaluate_s1(seed=SEED)
        assert "RQ1" in result.rq_contributions
        assert "RQ2/H2" in result.rq_contributions

    def test_amr_adapter_round_trip(self) -> None:
        """AMR adapter (mm, y-up) round-trips correctly."""
        import numpy as np

        from psl.adapters.robots.amr.adapter import AMRAdapter

        amr = AMRAdapter()
        state: dict[str, object] = {
            "base_pos_mm": np.array([500.0, 300.0]),
            "base_yaw_deg": 90.0,
            "base_vel_mm_s": np.array([100.0, 0.0]),
            "base_yaw_rate_deg_s": 10.0,
            "time": 1.0,
        }
        ir = amr.to_ir(state)
        rt = amr.from_ir(ir)
        np.testing.assert_allclose(
            np.asarray(rt["base_pos_mm"]), np.asarray(state["base_pos_mm"]), atol=1e-8
        )
        assert abs(float(rt["base_yaw_deg"]) - float(state["base_yaw_deg"])) < 1e-6  # type: ignore[arg-type]

    def test_r2r_panda_to_amr(self) -> None:
        """R2R: Panda → IR → AMR translation works through heterogeneous adapters."""

        from psl.adapters.robots.amr.adapter import AMRAdapter
        from psl.adapters.robots.panda.adapter import PandaAdapter
        from sim.wrapper import MuJoCoSim

        sim = MuJoCoSim(seed=SEED)
        sim.step(100)
        panda = PandaAdapter(entity_id="p")
        AMRAdapter(entity_id="a")
        jpos = sim.get_joint_positions()
        ee_pos, ee_quat = sim.get_ee_pose()
        panda_state: dict[str, object] = {
            "joint_positions": jpos,
            "joint_velocities": sim.get_joint_velocities(),
            "ee_position": ee_pos,
            "ee_quaternion": ee_quat,
            "time": sim.time,
        }
        # Panda → IR
        ir = panda.to_ir(panda_state)
        # IR contains EE pose in world frame; AMR should receive that position
        assert "ee_pose" in ir.phytes


@pytest.mark.oracle
class TestS2LineChangeover:
    """S2: Line changeover — commutativity + safety gate."""

    def test_two_layer_success(self) -> None:
        result = evaluate_s2(seed=SEED)
        assert result.business_success, "S2 business goal failed"
        assert result.psl_success, (
            f"S2 PSL failed: {[bp for bp in result.breakpoints if not bp.passed]}"
        )
        assert result.overall_pass

    def test_impossible_recipe_rejected(self) -> None:
        result = evaluate_s2(seed=SEED)
        gate_bp = next(bp for bp in result.breakpoints if bp.name == "safety_gate_rejection")
        assert gate_bp.passed, "Safety gate did not reject impossible recipe"

    def test_commutativity_within_threshold(self) -> None:
        result = evaluate_s2(seed=SEED)
        comm_bp = next(bp for bp in result.breakpoints if bp.name == "commutativity_divergence")
        assert comm_bp.passed, (
            f"Commutativity divergence {comm_bp.metric_value} > {comm_bp.threshold}"
        )


@pytest.mark.oracle
class TestS3LabCustody:
    """S3: Lab custody — provenance chain."""

    def test_two_layer_success(self) -> None:
        result = evaluate_s3(seed=SEED)
        assert result.business_success, "S3 business goal failed"
        assert result.psl_success, (
            f"S3 PSL failed: {[bp for bp in result.breakpoints if not bp.passed]}"
        )
        assert result.overall_pass

    def test_provenance_ablation_breaks_custody(self) -> None:
        result = evaluate_s3(seed=SEED)
        ablation_bp = next(
            bp for bp in result.breakpoints if bp.name == "provenance_ablation_breaks_custody"
        )
        assert ablation_bp.passed


@pytest.mark.oracle
class TestS4FieldInspection:
    """S4: Field inspection — LOD fusion + bidirectional anchoring."""

    def test_two_layer_success(self) -> None:
        result = evaluate_s4(seed=SEED)
        assert result.business_success, "S4 business goal failed"
        assert result.psl_success, (
            f"S4 PSL failed: {[bp for bp in result.breakpoints if not bp.passed]}"
        )
        assert result.overall_pass

    def test_lod_levels_work(self) -> None:
        result = evaluate_s4(seed=SEED)
        lod_bp = next(bp for bp in result.breakpoints if bp.name == "lod_consistency")
        assert lod_bp.passed

    def test_drone_metrics(self) -> None:
        result = evaluate_s4(seed=SEED)
        assert "drone_rt_error" in result.metrics
        assert result.metrics["drone_rt_error"] < 1e-6
        assert result.metrics["drone_negotiation_feasible"] is True


@pytest.mark.oracle
class TestS5PharmaLogistics:
    """S5: Pharma logistics — neuro-symbolic + provenance poisoning."""

    def test_two_layer_success(self) -> None:
        result = evaluate_s5(seed=SEED)
        assert result.business_success, "S5 business goal failed"
        assert result.psl_success, (
            f"S5 PSL failed: {[bp for bp in result.breakpoints if not bp.passed]}"
        )
        assert result.overall_pass

    def test_poisoning_detected(self) -> None:
        result = evaluate_s5(seed=SEED)
        poison_bp = next(
            bp for bp in result.breakpoints if bp.name == "provenance_poisoning_detected"
        )
        assert poison_bp.passed


@pytest.mark.oracle
class TestS6EwasteDisassembly:
    """S6: E-waste disassembly — open-world affordance grounding."""

    def test_two_layer_success(self) -> None:
        result = evaluate_s6(seed=SEED)
        assert result.business_success, "S6 business goal failed"
        assert result.psl_success, (
            f"S6 PSL failed: {[bp for bp in result.breakpoints if not bp.passed]}"
        )
        assert result.overall_pass

    def test_embedding_beats_symbol_only(self) -> None:
        result = evaluate_s6(seed=SEED)
        assert result.metrics["advantage"] > 0, (
            "Embedding should outperform symbol-only on holdout"
        )

    def test_uses_real_vla_predictions(self) -> None:
        """Verify S6 uses actual VLA predict_affordances, not synthetic simulation."""
        result = evaluate_s6(seed=SEED)
        assert "vla_mode" in result.metrics, "S6 must report which VLA mode was used"
        assert "per_object_results" in result.metrics, "S6 must report per-object VLA predictions"
        per_obj = result.metrics["per_object_results"]
        assert isinstance(per_obj, list)
        assert len(per_obj) > 0
        for obj in per_obj:
            assert "vla_pred" in obj, f"Missing VLA prediction for {obj.get('object')}"


@pytest.mark.oracle
class TestS7DegradedOps:
    """S7: Degraded operations — clock skew + graceful degradation."""

    def test_two_layer_success(self) -> None:
        result = evaluate_s7(seed=SEED)
        assert result.business_success, "S7 business goal failed"
        assert result.psl_success, (
            f"S7 PSL failed: {[bp for bp in result.breakpoints if not bp.passed]}"
        )
        assert result.overall_pass

    def test_zero_causal_violations(self) -> None:
        result = evaluate_s7(seed=SEED)
        causal_bp = next(
            bp for bp in result.breakpoints if bp.name == "causal_ordering_violations"
        )
        assert causal_bp.metric_value == 0, f"Causal violations: {causal_bp.metric_value}"

    def test_degradation_is_monotonic(self) -> None:
        result = evaluate_s7(seed=SEED)
        deg_bp = next(bp for bp in result.breakpoints if bp.name == "graceful_degradation")
        assert deg_bp.passed
