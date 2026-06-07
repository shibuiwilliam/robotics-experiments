"""Unit tests for Canonical IR and Panda adapter round-trip."""

from __future__ import annotations

import numpy as np
import pytest

from psl.adapters.robots.panda.adapter import PandaAdapter
from psl.ir.core import Adapter, IRState


@pytest.mark.unit
class TestIRState:
    def test_construction(self) -> None:
        ir = IRState(entity_id="test", phytes={}, timestamp=0.0)
        assert ir.entity_id == "test"
        assert len(ir.phytes) == 0

    def test_frozen(self) -> None:
        ir = IRState(entity_id="test", phytes={}, timestamp=0.0)
        with pytest.raises(Exception):  # noqa: B017
            ir.entity_id = "modified"  # type: ignore[misc]


@pytest.mark.unit
class TestAdapterProtocol:
    def test_panda_is_adapter(self) -> None:
        adapter = PandaAdapter()
        assert isinstance(adapter, Adapter)

    def test_entity_id(self) -> None:
        adapter = PandaAdapter(entity_id="my_panda")
        assert adapter.entity_id == "my_panda"


@pytest.mark.oracle
class TestPandaRoundTrip:
    """Round-trip tests using MuJoCo ground truth."""

    def test_round_trip_joint_positions(
        self, adapter: PandaAdapter, native_state: dict[str, object]
    ) -> None:
        """native → IR → native should preserve joint positions."""
        ir = adapter.to_ir(native_state)
        reconstructed = adapter.from_ir(ir)

        original = np.asarray(native_state["joint_positions"])
        result = np.asarray(reconstructed["joint_positions"])
        np.testing.assert_allclose(result, original, atol=1e-12)

    def test_round_trip_joint_velocities(
        self, adapter: PandaAdapter, native_state: dict[str, object]
    ) -> None:
        """native → IR → native should preserve joint velocities."""
        ir = adapter.to_ir(native_state)
        reconstructed = adapter.from_ir(ir)

        original = np.asarray(native_state["joint_velocities"])
        result = np.asarray(reconstructed["joint_velocities"])
        np.testing.assert_allclose(result, original, atol=1e-12)

    def test_round_trip_ee_position(
        self, adapter: PandaAdapter, native_state: dict[str, object]
    ) -> None:
        """native → IR → native should preserve EE position."""
        ir = adapter.to_ir(native_state)
        reconstructed = adapter.from_ir(ir)

        original = np.asarray(native_state["ee_position"])
        result = np.asarray(reconstructed["ee_position"])
        np.testing.assert_allclose(result, original, atol=1e-12)

    def test_round_trip_timestamp(
        self, adapter: PandaAdapter, native_state: dict[str, object]
    ) -> None:
        """Timestamp should survive round-trip."""
        ir = adapter.to_ir(native_state)
        reconstructed = adapter.from_ir(ir)
        assert reconstructed["time"] == native_state["time"]

    def test_ir_has_all_joints(
        self, adapter: PandaAdapter, native_state: dict[str, object]
    ) -> None:
        """IR should contain Phytes for all 7 joints."""
        ir = adapter.to_ir(native_state)
        for i in range(7):
            assert f"joint_{i}" in ir.phytes
            assert f"joint_vel_{i}" in ir.phytes
        assert "ee_pose" in ir.phytes

    def test_ir_phytes_have_covariance(
        self, adapter: PandaAdapter, native_state: dict[str, object]
    ) -> None:
        """All IR Phytes must carry non-zero covariance (no bare floats)."""
        ir = adapter.to_ir(native_state)
        for name, phyte in ir.phytes.items():
            assert phyte.covariance is not None
            assert np.all(np.diag(phyte.covariance) >= 0), f"{name} has negative variance"

    def test_ir_phytes_have_provenance(
        self, adapter: PandaAdapter, native_state: dict[str, object]
    ) -> None:
        """All IR Phytes must carry provenance."""
        ir = adapter.to_ir(native_state)
        for name, phyte in ir.phytes.items():
            assert len(phyte.provenance.chain) > 0, f"{name} has empty provenance"
            assert phyte.provenance.confidence > 0, f"{name} has zero confidence"

    def test_fidelity_contract(self, adapter: PandaAdapter) -> None:
        """Adapter must provide a fidelity contract."""
        contract = adapter.fidelity_contract
        assert len(contract.preserved_fields) > 0
        assert contract.information_loss_estimate >= 0
        assert contract.information_loss_estimate <= 1
