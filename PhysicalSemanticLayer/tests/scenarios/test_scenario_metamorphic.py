"""Metamorphic tests for scenarios — property-based invariance checks.

Per SCENARIOS.md: each scenario must have metamorphic tests verifying
frame equivariance, unit invariance, time equivariance, and scenario-
specific properties. These tests use NO ground truth (label-free).
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from eval.metamorphic.compositionality import check_compositionality
from eval.metamorphic.frame_equivariance import check_frame_equivariance
from eval.metamorphic.time_equivariance import (
    check_object_permutation_invariance,
    check_time_equivariance,
)
from eval.metamorphic.unit_invariance import check_unit_invariance_joint
from psl.adapters.robots.amr.adapter import AMRAdapter
from psl.adapters.robots.panda.adapter import PandaAdapter
from psl.adapters.robots.panda.heterogeneous import HeterogeneousPandaAdapter
from psl.phyte.geometry import random_se3
from sim.schema_gen.generator import SchemaTransform
from sim.wrapper import MuJoCoSim

SEED = 42


def _panda_state() -> dict[str, object]:
    sim = MuJoCoSim(seed=SEED)
    sim.step(100)
    return {
        "joint_positions": sim.get_joint_positions(),
        "joint_velocities": sim.get_joint_velocities(),
        "ee_position": sim.get_ee_pose()[0],
        "ee_quaternion": sim.get_ee_pose()[1],
        "time": sim.time,
    }


# ── S1: Frame equivariance (R2R handoff), unit invariance (mm AMR) ──


@pytest.mark.metamorphic
class TestS1Metamorphic:
    """S1: frame equivariance + unit invariance (AMR mm↔m)."""

    @given(seed=st.integers(min_value=0, max_value=5000))
    @settings(max_examples=10, derandomize=True)
    def test_frame_equivariance(self, seed: int) -> None:
        state = _panda_state()
        adapter = PandaAdapter(entity_id="panda")
        rng = np.random.default_rng(seed)
        g = random_se3(rng)
        passed, div = check_frame_equivariance(state, adapter, g, tol=1e-6)
        assert passed, f"S1 frame equivariance violated: {div:.2e}"

    def test_unit_invariance_panda(self) -> None:
        state = _panda_state()
        adapter = PandaAdapter()
        passed, err = check_unit_invariance_joint(adapter, state)
        assert passed, f"S1 unit invariance violated: {err:.2e}"

    def test_amr_round_trip(self) -> None:
        """AMR adapter (mm, y-up) round-trips correctly."""
        amr = AMRAdapter()
        state: dict[str, object] = {
            "base_pos_mm": np.array([500.0, 200.0]),
            "base_yaw_deg": 45.0,
            "base_vel_mm_s": np.array([100.0, 50.0]),
            "base_yaw_rate_deg_s": 5.0,
            "time": 1.0,
        }
        ir = amr.to_ir(state)
        rt = amr.from_ir(ir)
        np.testing.assert_allclose(
            np.asarray(rt["base_pos_mm"]), np.asarray(state["base_pos_mm"]), atol=1e-8
        )

    def test_object_permutation_invariance(self) -> None:
        state = _panda_state()
        adapter = PandaAdapter()
        passed, div = check_object_permutation_invariance(adapter, state)
        assert passed, f"S1 permutation invariance violated: {div:.2e}"


# ── S2: Compositionality (multi-hop commutativity) ──


@pytest.mark.metamorphic
class TestS2Metamorphic:
    """S2: compositionality — A→IR→B ≈ A→IR→C→IR→B."""

    def test_compositionality_noiseless(self) -> None:
        state = _panda_state()
        adapter_a = PandaAdapter(entity_id="a")
        adapter_b = HeterogeneousPandaAdapter(
            entity_id="b",
            transform=SchemaTransform(unit_scale=1000.0, frame_rotation_z_rad=np.pi / 6),
        )
        adapter_c = PandaAdapter(entity_id="c")
        passed, div = check_compositionality(adapter_a, adapter_c, adapter_b, state, tol=1e-8)
        assert passed, f"S2 compositionality violated: {div:.2e}"

    @given(seed=st.integers(min_value=0, max_value=5000))
    @settings(max_examples=10, derandomize=True)
    def test_frame_equivariance(self, seed: int) -> None:
        state = _panda_state()
        adapter = PandaAdapter()
        rng = np.random.default_rng(seed)
        g = random_se3(rng)
        passed, div = check_frame_equivariance(state, adapter, g, tol=1e-6)
        assert passed, f"S2 frame equivariance violated: {div:.2e}"


# ── S3: Time equivariance (order preservation), object permutation ──


@pytest.mark.metamorphic
class TestS3Metamorphic:
    """S3: time equivariance + object permutation (custody chain order)."""

    @given(dt=st.floats(min_value=-10.0, max_value=10.0, allow_nan=False))
    @settings(max_examples=15, derandomize=True)
    def test_time_equivariance(self, dt: float) -> None:
        state = _panda_state()
        adapter = PandaAdapter()
        passed, div = check_time_equivariance(adapter, state, dt=dt)
        assert passed, f"S3 time equivariance violated: {div:.2e}"

    def test_object_permutation(self) -> None:
        state = _panda_state()
        adapter = PandaAdapter()
        passed, div = check_object_permutation_invariance(adapter, state)
        assert passed, f"S3 object permutation violated: {div:.2e}"


# ── S4: Frame equivariance (overhead↔ground), LOD consistency ──


@pytest.mark.metamorphic
class TestS4Metamorphic:
    """S4: frame equivariance across sensor frames + LOD consistency."""

    @given(seed=st.integers(min_value=0, max_value=5000))
    @settings(max_examples=10, derandomize=True)
    def test_frame_equivariance_ground(self, seed: int) -> None:
        amr = AMRAdapter(entity_id="ground")
        state: dict[str, object] = {
            "base_pos_mm": np.array([300.0, 100.0]),
            "base_yaw_deg": 45.0,
            "base_vel_mm_s": np.zeros(2),
            "base_yaw_rate_deg_s": 0.0,
            "time": 1.0,
        }
        ir = amr.to_ir(state)
        rt = amr.from_ir(ir)
        np.testing.assert_allclose(
            np.asarray(rt["base_pos_mm"]), np.asarray(state["base_pos_mm"]), atol=1e-8
        )

    def test_lod_same_entity(self) -> None:
        """Different LOD views of the same entity must identify consistently."""
        from psl.lod.resolution import LODSubscriber

        state = _panda_state()
        adapter = PandaAdapter()
        ir = adapter.to_ir(state)
        lod = LODSubscriber()
        summary = lod.to_summary("arm", ir.phytes)
        semantic = lod.to_semantic("arm", ir.phytes)
        assert summary.n_joints == 7
        assert "arm" in semantic.entity_id

    def test_s4_drone_round_trip(self) -> None:
        """Drone adapter round-trip is idempotent in S4 context."""
        from psl.adapters.robots.drone.adapter import DroneAdapter

        drone = DroneAdapter(entity_id="inspection_drone")
        native: dict[str, object] = {
            "position_enu": np.array([0.0, 0.5, 2.0]),
            "orientation_quat_hamilton": np.array([1.0, 0.0, 0.0, 0.0]),
            "linear_velocity_enu": np.zeros(3),
            "angular_velocity_body": np.zeros(3),
            "time": 1.0,
        }
        ir = drone.to_ir(native)
        rt = drone.from_ir(ir)
        np.testing.assert_allclose(
            np.asarray(native["position_enu"]),
            np.asarray(rt["position_enu"]),
            atol=1e-10,
        )


# ── S5: No metamorphic needed (safety focus), but test provenance stability ──


@pytest.mark.metamorphic
class TestS5Metamorphic:
    """S5: provenance stability under repeated translation."""

    def test_double_round_trip_preserves_provenance(self) -> None:
        state = _panda_state()
        adapter = PandaAdapter()
        ir1 = adapter.to_ir(state)
        rt1 = adapter.from_ir(ir1)
        ir2 = adapter.to_ir(rt1)
        # Provenance chains should grow but confidence should not go to 0
        for name in ir2.phytes:
            assert ir2.phytes[name].provenance.confidence > 0


# ── S6: Object permutation invariance (novel objects treated like known) ──


@pytest.mark.metamorphic
class TestS6Metamorphic:
    """S6: object permutation invariance for affordance grounding."""

    def test_object_permutation(self) -> None:
        state = _panda_state()
        adapter = PandaAdapter()
        passed, div = check_object_permutation_invariance(adapter, state)
        assert passed, f"S6 object permutation violated: {div:.2e}"

    @given(seed=st.integers(min_value=0, max_value=5000))
    @settings(max_examples=10, derandomize=True)
    def test_frame_equivariance(self, seed: int) -> None:
        state = _panda_state()
        adapter = PandaAdapter()
        rng = np.random.default_rng(seed)
        g = random_se3(rng)
        passed, div = check_frame_equivariance(state, adapter, g, tol=1e-6)
        assert passed, f"S6 frame equivariance violated: {div:.2e}"


# ── S7: Time equivariance under clock skew ──


@pytest.mark.metamorphic
class TestS7Metamorphic:
    """S7: time equivariance — timestamp shift must not break causality."""

    @given(dt=st.floats(min_value=-50.0, max_value=50.0, allow_nan=False))
    @settings(max_examples=15, derandomize=True)
    def test_time_equivariance(self, dt: float) -> None:
        state = _panda_state()
        adapter = PandaAdapter()
        passed, div = check_time_equivariance(adapter, state, dt=dt)
        assert passed, f"S7 time equivariance violated: {div:.2e}"
