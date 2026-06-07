"""Tests for ClaudeAgentAdapter — agent-side N+N translation."""

from __future__ import annotations

import numpy as np
import pytest

from psl.adapters.agents.claude.adapter import ClaudeAgentAdapter
from psl.ir.core import Adapter, IRState


@pytest.mark.unit
class TestClaudeAgentAdapter:
    def test_implements_protocol(self) -> None:
        adapter = ClaudeAgentAdapter(entity_id="test_agent")
        assert isinstance(adapter, Adapter)

    def test_entity_id(self) -> None:
        adapter = ClaudeAgentAdapter(entity_id="my_agent")
        assert adapter.entity_id == "my_agent"

    def test_to_ir_produces_valid_irstate(self) -> None:
        adapter = ClaudeAgentAdapter()
        native = {
            "task_plan": "Pick blue gear from Bin C and place in QA tray",
            "decision": "Execute pick sequence",
            "confidence": 0.95,
            "referenced_entities": ["panda_arm", "blue_gear"],
            "timestamp": 10.0,
            "tool_calls": [{"tool": "query_world_model", "args": {"entity_id": "panda_arm"}}],
        }
        ir = adapter.to_ir(native)
        assert isinstance(ir, IRState)
        assert ir.entity_id == "claude_agent"
        assert ir.timestamp == 10.0
        assert ir.clock_domain == "agent"
        assert "task_plan" in ir.phytes
        assert "decision" in ir.phytes
        assert "agent_reference:panda_arm" in ir.phytes
        assert "agent_reference:blue_gear" in ir.phytes

    def test_phytes_have_covariance_and_provenance(self) -> None:
        adapter = ClaudeAgentAdapter()
        native = {
            "task_plan": "Move to bin",
            "decision": "go",
            "confidence": 0.9,
            "referenced_entities": ["arm"],
            "timestamp": 1.0,
            "tool_calls": [],
        }
        ir = adapter.to_ir(native)
        for phyte in ir.phytes.values():
            assert phyte.covariance is not None
            assert phyte.provenance is not None
            assert phyte.provenance.confidence > 0

    def test_from_ir_reconstructs_summary(self) -> None:
        adapter = ClaudeAgentAdapter()
        native = {
            "task_plan": "test plan",
            "decision": "test decision",
            "confidence": 0.8,
            "referenced_entities": ["entity_a"],
            "timestamp": 5.0,
            "tool_calls": [],
        }
        ir = adapter.to_ir(native)
        reconstructed = adapter.from_ir(ir)
        assert "summary" in reconstructed
        assert "entity_states" in reconstructed
        assert reconstructed["timestamp"] == 5.0

    def test_round_trip_preserves_timestamp(self) -> None:
        adapter = ClaudeAgentAdapter()
        native = {
            "task_plan": "plan",
            "decision": "decide",
            "confidence": 0.85,
            "referenced_entities": [],
            "timestamp": 42.0,
            "tool_calls": [],
        }
        ir = adapter.to_ir(native)
        result = adapter.from_ir(ir)
        assert result["timestamp"] == 42.0

    def test_fidelity_contract(self) -> None:
        adapter = ClaudeAgentAdapter()
        contract = adapter.fidelity_contract
        assert contract.information_loss_estimate == 0.4
        assert "raw_sensor_values" in contract.lost_fields
        assert "timestamp" in contract.preserved_fields

    def test_deterministic_encoding(self) -> None:
        a1 = ClaudeAgentAdapter()
        a2 = ClaudeAgentAdapter()
        native = {
            "task_plan": "same plan",
            "decision": "same decision",
            "confidence": 0.9,
            "referenced_entities": ["x"],
            "timestamp": 1.0,
            "tool_calls": [],
        }
        ir1 = a1.to_ir(native)
        ir2 = a2.to_ir(native)
        np.testing.assert_array_equal(
            ir1.phytes["task_plan"].value,
            ir2.phytes["task_plan"].value,
        )

    def test_negotiation_with_panda(self) -> None:
        from psl.negotiation.handshake import (
            CapabilityDescriptor,
            ControlMode,
            SemanticNegotiator,
        )

        neg = SemanticNegotiator()
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
        neg.register(
            CapabilityDescriptor(
                entity_id="agent",
                entity_type="agent",
                n_joints=0,
                control_modes=(),
                frame_convention="z_up",
                unit_system="SI",
                semantic_capabilities=("plan", "decide", "query"),
            )
        )
        result = neg.negotiate("panda", "agent")
        assert result.feasible
