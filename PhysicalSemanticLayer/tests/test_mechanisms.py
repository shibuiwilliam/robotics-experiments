"""Tests for mechanisms 7, 8, 10 + causality gate + ablations.

Covers the previously-stub modules: anchoring, negotiation, LOD,
plus the new causality check in the safety gate.
"""

from __future__ import annotations

import numpy as np
import pytest

from eval.ablations import run_ablation_study
from pseudo_cloud.data import init_db
from psl.anchoring.resolver import PhysicalAnchorResolver
from psl.lod.resolution import LODSubscriber
from psl.negotiation.handshake import CapabilityDescriptor, SemanticNegotiator
from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3
from psl.safety.gate import PhysicsConsistencyGate


@pytest.mark.unit
class TestDocumentAnchoring:
    def test_resolve_bin_c(self) -> None:
        conn = init_db()
        resolver = PhysicalAnchorResolver(conn)
        phyte = resolver.resolve_bin("C", timestamp=1.0)
        assert phyte is not None
        assert phyte.frame == "world"
        assert phyte.unit == "m"
        np.testing.assert_allclose(phyte.value, [0.3, 0.3, 0.45])
        assert phyte.provenance.confidence == pytest.approx(0.9)
        assert len(phyte.provenance.chain) == 1

    def test_resolve_nonexistent_bin(self) -> None:
        conn = init_db()
        resolver = PhysicalAnchorResolver(conn)
        assert resolver.resolve_bin("NONEXISTENT") is None

    def test_resolve_work_order(self) -> None:
        conn = init_db()
        resolver = PhysicalAnchorResolver(conn)
        result = resolver.resolve_work_order("WO-42", timestamp=2.0)
        assert result["source"] is not None
        assert result["target"] is not None
        # Source is Bin C, target is QA_TRAY
        np.testing.assert_allclose(result["source"].value, [0.3, 0.3, 0.45])  # type: ignore[union-attr]
        np.testing.assert_allclose(result["target"].value, [0.7, 0.0, 0.45])  # type: ignore[union-attr]
        # Provenance should have 2 entries (bin resolve + WO resolve)
        assert len(result["source"].provenance.chain) == 2  # type: ignore[union-attr]

    def test_resolve_item_location(self) -> None:
        conn = init_db()
        resolver = PhysicalAnchorResolver(conn)
        phyte = resolver.resolve_item_location("gear_blue_001", timestamp=3.0)
        assert phyte is not None
        # Blue gear is in Bin C
        np.testing.assert_allclose(phyte.value, [0.3, 0.3, 0.45])
        # Provenance: bin resolve + item resolve
        assert len(phyte.provenance.chain) == 2

    def test_anchored_phyte_has_uncertainty(self) -> None:
        """Document-derived positions must carry uncertainty (not bare floats)."""
        conn = init_db()
        resolver = PhysicalAnchorResolver(conn, position_uncertainty_m=0.1)
        phyte = resolver.resolve_bin("A")
        assert phyte is not None
        assert np.all(np.diag(phyte.covariance) > 0)
        assert phyte.covariance[0, 0] == pytest.approx(0.01)  # 0.1^2


# ── Mechanism 8: Semantic Negotiation ──


@pytest.mark.unit
class TestSemanticNegotiation:
    def test_register_and_lookup(self) -> None:
        neg = SemanticNegotiator()
        desc = CapabilityDescriptor(entity_id="panda_a", entity_type="robot", n_joints=7)
        neg.register(desc)
        assert neg.get("panda_a") is not None
        assert "panda_a" in neg.list_participants()

    def test_identical_robots_negotiate(self) -> None:
        neg = SemanticNegotiator()
        neg.register(CapabilityDescriptor(entity_id="a", entity_type="robot", n_joints=7))
        neg.register(CapabilityDescriptor(entity_id="b", entity_type="robot", n_joints=7))
        result = neg.negotiate("a", "b")
        assert result.feasible
        assert len(result.translation_notes) == 0

    def test_frame_mismatch_noted(self) -> None:
        neg = SemanticNegotiator()
        neg.register(
            CapabilityDescriptor(entity_id="a", entity_type="robot", frame_convention="z_up")
        )
        neg.register(
            CapabilityDescriptor(entity_id="b", entity_type="robot", frame_convention="y_up")
        )
        result = neg.negotiate("a", "b")
        assert result.feasible  # Still feasible, just needs transform
        assert any("Frame mismatch" in n for n in result.translation_notes)

    def test_unit_mismatch_noted(self) -> None:
        neg = SemanticNegotiator()
        neg.register(CapabilityDescriptor(entity_id="a", entity_type="robot", unit_system="SI"))
        neg.register(
            CapabilityDescriptor(entity_id="b", entity_type="robot", unit_system="imperial")
        )
        result = neg.negotiate("a", "b")
        assert any("Unit mismatch" in n for n in result.translation_notes)

    def test_unregistered_entity(self) -> None:
        neg = SemanticNegotiator()
        neg.register(CapabilityDescriptor(entity_id="a", entity_type="robot"))
        result = neg.negotiate("a", "nonexistent")
        assert not result.feasible

    def test_robot_agent_negotiation(self) -> None:
        neg = SemanticNegotiator()
        neg.register(
            CapabilityDescriptor(
                entity_id="panda",
                entity_type="robot",
                n_joints=7,
                semantic_capabilities=("grasp", "place"),
            )
        )
        neg.register(
            CapabilityDescriptor(
                entity_id="supervisor",
                entity_type="agent",
                semantic_capabilities=("plan", "delegate"),
            )
        )
        result = neg.negotiate("panda", "supervisor")
        assert result.feasible  # Agent-robot always feasible

    def test_joint_count_mismatch(self) -> None:
        neg = SemanticNegotiator()
        neg.register(CapabilityDescriptor(entity_id="a", entity_type="robot", n_joints=7))
        neg.register(CapabilityDescriptor(entity_id="b", entity_type="robot", n_joints=6))
        result = neg.negotiate("a", "b")
        assert any("Joint count" in n for n in result.translation_notes)


# ── Mechanism 7: LOD Multi-Resolution ──


@pytest.mark.unit
class TestLODResolution:
    def _make_phytes(self) -> dict[str, Phyte]:
        phytes: dict[str, Phyte] = {}
        for i in range(3):
            phytes[f"joint_{i}"] = Phyte(
                semantic_id=f"joint_position_{i}",
                frame="world",
                pose=identity_se3(),
                timestamp=1.0,
                unit="rad",
                value=np.array([0.1 * (i + 1)]),
                covariance=np.array([[1e-4]]),
            )
        phytes["ee_pose"] = Phyte(
            semantic_id="end_effector_pose",
            frame="world",
            pose=identity_se3(),
            timestamp=1.0,
            unit="m",
            value=np.array([0.5, 0.1, 0.8, 1.0, 0.0, 0.0, 0.0]),
            covariance=np.eye(7) * 1e-4,
        )
        return phytes

    def test_raw_returns_all_phytes(self) -> None:
        lod = LODSubscriber()
        phytes = self._make_phytes()
        raw = lod.to_raw(phytes)
        assert len(raw) == len(phytes)
        assert "joint_0" in raw

    def test_summary_aggregates(self) -> None:
        lod = LODSubscriber()
        phytes = self._make_phytes()
        summary = lod.to_summary("robot_a", phytes)
        assert summary.n_joints == 3
        assert summary.joint_pos_mean == pytest.approx(0.2)  # mean(0.1, 0.2, 0.3)
        assert summary.ee_position is not None
        assert summary.ee_position[0] == pytest.approx(0.5)

    def test_semantic_produces_description(self) -> None:
        lod = LODSubscriber()
        phytes = self._make_phytes()
        sem = lod.to_semantic("robot_a", phytes)
        assert "robot_a" in sem.description
        assert "3 joints" in sem.description
        assert "movable" in sem.affordances

    def test_empty_phytes(self) -> None:
        lod = LODSubscriber()
        summary = lod.to_summary("empty", {})
        assert summary.n_joints == 0
        assert summary.ee_position is None


# ── Causality Check in Safety Gate ──


@pytest.mark.unit
class TestCausalityCheck:
    def _make_phyte(self, val: float, t: float, domain: str = "sim", t_unc: float = 0.0) -> Phyte:
        return Phyte(
            semantic_id="joint_0",
            frame="world",
            pose=identity_se3(),
            timestamp=t,
            clock_domain=domain,
            time_uncertainty=t_unc,
            unit="rad",
            value=np.array([val]),
            covariance=np.array([[1e-8]]),
        )

    def test_accept_forward_time(self) -> None:
        gate = PhysicsConsistencyGate()
        prev = {"j": self._make_phyte(0.0, 1.0)}
        new = {"j": self._make_phyte(0.1, 2.0)}
        result = gate.check(new, prev)
        assert result.accepted

    def test_reject_backward_time_same_domain(self) -> None:
        gate = PhysicsConsistencyGate()
        prev = {"j": self._make_phyte(0.0, 2.0)}
        new = {"j": self._make_phyte(0.1, 1.0)}  # Goes backward!
        result = gate.check(new, prev)
        assert not result.accepted
        assert any("causal ordering" in v for v in result.violations)

    def test_allow_cross_domain_with_slack(self) -> None:
        """Different clock domains: allow backward if within time_uncertainty."""
        gate = PhysicsConsistencyGate()
        prev = {"j": self._make_phyte(0.0, 2.0, domain="sim", t_unc=0.5)}
        new = {"j": self._make_phyte(0.1, 1.8, domain="wall", t_unc=0.5)}
        result = gate.check(new, prev)
        assert result.accepted  # 1.8 > 2.0 - (0.5+0.5) = 1.0

    def test_reject_cross_domain_beyond_slack(self) -> None:
        gate = PhysicsConsistencyGate()
        prev = {"j": self._make_phyte(0.0, 5.0, domain="sim", t_unc=0.1)}
        new = {"j": self._make_phyte(0.1, 1.0, domain="wall", t_unc=0.1)}
        result = gate.check(new, prev)
        assert not result.accepted
        assert any("cross-domain causal" in v for v in result.violations)


# ── Ablation Framework ──


@pytest.mark.oracle
class TestAblations:
    def test_ablation_study_runs(self) -> None:
        results = run_ablation_study(seed=42, n_steps=100)
        assert len(results) == 5
        conditions = [r.condition for r in results]
        assert "full_psl" in conditions
        assert "no_covariance" in conditions
        assert "no_provenance" in conditions
        assert "no_safety_gate" in conditions
        assert "no_contracts" in conditions

    def test_full_psl_is_baseline(self) -> None:
        results = run_ablation_study(seed=42, n_steps=100)
        full = next(r for r in results if r.condition == "full_psl")
        assert full.has_covariance
        assert full.has_provenance
        assert full.safety_gate_active
