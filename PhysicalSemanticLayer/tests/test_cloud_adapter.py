"""Tests for CloudDataAdapter — business data N+N translation."""

from __future__ import annotations

import numpy as np
import pytest

from psl.adapters.cloud.adapter import CloudDataAdapter
from psl.ir.core import Adapter


def _make_cloud_native() -> dict[str, object]:
    return {
        "bin_positions": {
            "Bin_A": [0.5, 0.0, 0.8],
            "Bin_B": [0.5, 0.3, 0.8],
            "Bin_C": [0.5, -0.3, 0.8],
        },
        "work_orders": [
            {"id": "WO-42", "status": "in_progress", "item_id": "GEAR-001", "priority": 1},
            {"id": "WO-43", "status": "pending", "item_id": "BOLT-002", "priority": 2},
        ],
        "inventory": [
            {"item_id": "GEAR-001", "name": "Blue Gear", "bin": "Bin_C", "quantity": 50},
        ],
        "timestamp": 100.0,
    }


@pytest.mark.unit
class TestCloudDataAdapter:
    def test_implements_protocol(self) -> None:
        adapter = CloudDataAdapter()
        assert isinstance(adapter, Adapter)

    def test_entity_id(self) -> None:
        adapter = CloudDataAdapter(entity_id="my_cloud")
        assert adapter.entity_id == "my_cloud"

    def test_to_ir_creates_bin_phytes(self) -> None:
        adapter = CloudDataAdapter()
        ir = adapter.to_ir(_make_cloud_native())
        assert "bin:Bin_A" in ir.phytes
        assert "bin:Bin_B" in ir.phytes
        assert "bin:Bin_C" in ir.phytes
        assert ir.phytes["bin:Bin_A"].unit == "m"
        assert ir.phytes["bin:Bin_A"].frame == "world"

    def test_to_ir_creates_work_order_phytes(self) -> None:
        adapter = CloudDataAdapter()
        ir = adapter.to_ir(_make_cloud_native())
        assert "work_order:WO-42" in ir.phytes
        assert "work_order:WO-43" in ir.phytes

    def test_bin_phyte_has_document_uncertainty(self) -> None:
        adapter = CloudDataAdapter()
        ir = adapter.to_ir(_make_cloud_native())
        cov = ir.phytes["bin:Bin_A"].covariance
        assert cov[0, 0] == pytest.approx(0.05**2)

    def test_phytes_have_provenance(self) -> None:
        adapter = CloudDataAdapter()
        ir = adapter.to_ir(_make_cloud_native())
        for phyte in ir.phytes.values():
            assert phyte.provenance.confidence > 0
            assert len(phyte.provenance.chain) > 0

    def test_round_trip_bin_positions(self) -> None:
        adapter = CloudDataAdapter()
        native = _make_cloud_native()
        ir = adapter.to_ir(native)
        result = adapter.from_ir(ir)
        assert "Bin_A" in result["bin_positions"]
        np.testing.assert_allclose(result["bin_positions"]["Bin_A"], [0.5, 0.0, 0.8], atol=1e-10)

    def test_round_trip_work_order_status(self) -> None:
        adapter = CloudDataAdapter()
        native = _make_cloud_native()
        ir = adapter.to_ir(native)
        result = adapter.from_ir(ir)
        assert result["work_order_statuses"]["WO-42"] == "in_progress"
        assert result["work_order_statuses"]["WO-43"] == "pending"

    def test_fidelity_contract(self) -> None:
        adapter = CloudDataAdapter()
        c = adapter.fidelity_contract
        assert c.information_loss_estimate == 0.3
        assert "full_document_text" in c.lost_fields
        assert "position" in c.preserved_fields

    def test_timestamp_preserved(self) -> None:
        adapter = CloudDataAdapter()
        ir = adapter.to_ir(_make_cloud_native())
        result = adapter.from_ir(ir)
        assert result["timestamp"] == 100.0

    def test_clock_domain_is_wall(self) -> None:
        adapter = CloudDataAdapter()
        ir = adapter.to_ir(_make_cloud_native())
        assert ir.clock_domain == "wall"
        for phyte in ir.phytes.values():
            assert phyte.clock_domain == "wall"
