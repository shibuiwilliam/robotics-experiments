"""Tests for DroneAdapter — 6-DOF quadrotor N+N translation."""

from __future__ import annotations

import numpy as np
import pytest

from psl.adapters.robots.drone.adapter import DroneAdapter
from psl.ir.core import Adapter, IRState


def _make_native(
    pos: tuple[float, float, float] = (1.0, 2.0, 3.0),
    quat: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0),
    linvel: tuple[float, float, float] = (0.1, 0.2, 0.3),
    angvel: tuple[float, float, float] = (0.01, 0.02, 0.03),
    time: float = 1.0,
) -> dict[str, object]:
    return {
        "position_enu": np.array(pos),
        "orientation_quat_hamilton": np.array(quat),
        "linear_velocity_enu": np.array(linvel),
        "angular_velocity_body": np.array(angvel),
        "time": time,
    }


@pytest.mark.unit
class TestDroneAdapter:
    def test_implements_protocol(self) -> None:
        adapter = DroneAdapter(entity_id="test_drone")
        assert isinstance(adapter, Adapter)

    def test_entity_id(self) -> None:
        adapter = DroneAdapter(entity_id="my_drone")
        assert adapter.entity_id == "my_drone"

    def test_to_ir_produces_valid_irstate(self) -> None:
        adapter = DroneAdapter()
        ir = adapter.to_ir(_make_native())
        assert isinstance(ir, IRState)
        assert "base_pose" in ir.phytes
        assert "linear_velocity" in ir.phytes
        assert "angular_velocity" in ir.phytes
        assert ir.timestamp == 1.0
        assert ir.clock_domain == "sim"

    def test_phytes_have_covariance_and_provenance(self) -> None:
        adapter = DroneAdapter()
        ir = adapter.to_ir(_make_native())
        for phyte in ir.phytes.values():
            assert phyte.covariance is not None
            assert phyte.provenance is not None
            assert phyte.provenance.confidence > 0

    def test_round_trip_noiseless(self) -> None:
        adapter = DroneAdapter()
        native = _make_native()
        ir = adapter.to_ir(native)
        result = adapter.from_ir(ir)

        np.testing.assert_allclose(
            np.asarray(result["position_enu"]),
            np.asarray(native["position_enu"]),
            atol=1e-6,
        )
        np.testing.assert_allclose(
            np.asarray(result["linear_velocity_enu"]),
            np.asarray(native["linear_velocity_enu"]),
            atol=1e-6,
        )
        assert result["time"] == native["time"]

    def test_identity_rotation_preserved(self) -> None:
        adapter = DroneAdapter()
        native = _make_native(quat=(1.0, 0.0, 0.0, 0.0))
        ir = adapter.to_ir(native)
        result = adapter.from_ir(ir)
        quat_rt = np.asarray(result["orientation_quat_hamilton"])
        # Identity or equivalent
        assert abs(abs(quat_rt[0]) - 1.0) < 1e-6

    def test_enu_to_world_frame_conversion(self) -> None:
        adapter = DroneAdapter()
        # ENU pos (1, 0, 0) = East → should map to world y direction (or similar)
        native = _make_native(pos=(1.0, 0.0, 0.0))
        ir = adapter.to_ir(native)
        world_pos = ir.phytes["base_pose"].value[:3]
        # ENU x(East) maps to world y via 90° rotation
        assert abs(world_pos[1] - (-1.0)) < 1e-6 or abs(world_pos[0] - 0.0) < 1e-6

    def test_fidelity_contract(self) -> None:
        adapter = DroneAdapter()
        c = adapter.fidelity_contract
        assert "rotor_speeds" in c.lost_fields
        assert c.information_loss_estimate == 0.05
        assert "value" in c.preserved_fields

    def test_timestamp_preserved(self) -> None:
        adapter = DroneAdapter()
        native = _make_native(time=99.5)
        ir = adapter.to_ir(native)
        result = adapter.from_ir(ir)
        assert result["time"] == 99.5

    def test_cross_adapter_r2r_with_panda(self) -> None:
        """Verify drone and panda can translate through IR (N+N proof)."""
        from psl.adapters.robots.panda.adapter import PandaAdapter

        drone = DroneAdapter(entity_id="drone")
        panda = PandaAdapter(entity_id="panda")

        drone_native = _make_native()
        drone_ir = drone.to_ir(drone_native)

        # Panda can read from drone's IR (loses joint positions, keeps what it can)
        panda_native = panda.from_ir(drone_ir)
        assert "joint_positions" in panda_native
        assert "time" in panda_native

    def test_negotiation_with_panda(self) -> None:
        from psl.negotiation.handshake import (
            CapabilityDescriptor,
            ControlMode,
            SemanticNegotiator,
        )

        neg = SemanticNegotiator()
        neg.register(
            CapabilityDescriptor(
                entity_id="drone",
                entity_type="robot",
                n_joints=0,
                control_modes=(ControlMode.VELOCITY,),
                frame_convention="enu",
                unit_system="SI",
                semantic_capabilities=("fly", "inspect", "transport"),
            )
        )
        neg.register(
            CapabilityDescriptor(
                entity_id="panda",
                entity_type="robot",
                n_joints=7,
                control_modes=(ControlMode.POSITION,),
                frame_convention="z_up",
                unit_system="SI",
                semantic_capabilities=("grasp", "place"),
            )
        )
        result = neg.negotiate("drone", "panda")
        assert result.feasible
        # Should have notes about frame mismatch and joint count
        assert len(result.translation_notes) > 0


@pytest.mark.metamorphic
class TestDroneMetamorphic:
    def test_double_round_trip_idempotent(self) -> None:
        adapter = DroneAdapter()
        native = _make_native()
        ir1 = adapter.to_ir(native)
        rt1 = adapter.from_ir(ir1)
        ir2 = adapter.to_ir(rt1)
        rt2 = adapter.from_ir(ir2)
        np.testing.assert_allclose(
            np.asarray(rt1["position_enu"]),
            np.asarray(rt2["position_enu"]),
            atol=1e-10,
        )
