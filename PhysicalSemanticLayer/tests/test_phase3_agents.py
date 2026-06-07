"""Phase 3 tests: Pseudo-cloud, MCP tools, document anchoring.

Tests the pseudo-cloud data, MCP tool handlers (without API calls),
and the document-to-physical resolution pipeline.
"""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest

from agents.tools.psl_tools import (
    make_query_world_model_handler,
    make_resolve_document_handler,
    make_subscribe_affordances_handler,
)
from pseudo_cloud.data import (
    init_db,
    query_inventory,
    query_sop,
    query_work_order,
    resolve_bin_to_position,
)
from psl.adapters.robots.panda.adapter import PandaAdapter
from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3
from psl.world_model.core import WorldModel


@pytest.mark.unit
class TestPseudoCloud:
    def test_init_db(self) -> None:
        conn = init_db()
        wo = query_work_order(conn, "WO-42")
        assert wo is not None
        assert wo["item_id"] == "gear_blue_001"

    def test_query_inventory(self) -> None:
        conn = init_db()
        item = query_inventory(conn, "gear_blue_001")
        assert item is not None
        assert item["status"] == "defective"
        assert item["bin"] == "C"

    def test_resolve_bin(self) -> None:
        conn = init_db()
        pos = resolve_bin_to_position(conn, "C")
        assert pos is not None
        assert len(pos) == 3
        assert pos == [0.3, 0.3, 0.45]

    def test_resolve_qa_tray(self) -> None:
        conn = init_db()
        pos = resolve_bin_to_position(conn, "QA_TRAY")
        assert pos is not None

    def test_query_sop(self) -> None:
        conn = init_db()
        sop = query_sop(conn, "SOP-PICK-001")
        assert sop is not None
        assert len(sop["steps"]) == 8  # type: ignore[arg-type]

    def test_nonexistent_bin(self) -> None:
        conn = init_db()
        pos = resolve_bin_to_position(conn, "NONEXISTENT")
        assert pos is None


@pytest.mark.unit
class TestMCPToolHandlers:
    """Test MCP tool handlers directly (no API calls)."""

    def test_query_world_model_handler(self) -> None:
        wm = WorldModel()
        wm.register_entity("robot_0")
        phyte = Phyte(
            semantic_id="joint_0",
            frame="world",
            pose=identity_se3(),
            timestamp=0.0,
            unit="rad",
            value=np.array([0.5]),
            covariance=np.array([[1e-4]]),
        )
        wm.write("robot_0", {"joint_0": phyte})

        handler = make_query_world_model_handler(wm)
        result = asyncio.get_event_loop().run_until_complete(handler({"entity_id": "robot_0"}))
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert "joint_0" in data
        assert data["joint_0"]["value"] == [0.5]

    def test_query_world_model_not_found(self) -> None:
        wm = WorldModel()
        handler = make_query_world_model_handler(wm)
        result = asyncio.get_event_loop().run_until_complete(handler({"entity_id": "nonexistent"}))
        assert result.get("is_error") is True

    def test_resolve_document_bin(self) -> None:
        conn = init_db()
        handler = make_resolve_document_handler(conn)
        result = asyncio.get_event_loop().run_until_complete(
            handler({"reference_type": "bin", "reference_id": "C"})
        )
        data = json.loads(result["content"][0]["text"])
        assert data["resolved"] is True
        assert data["position"] == [0.3, 0.3, 0.45]

    def test_resolve_document_work_order(self) -> None:
        conn = init_db()
        handler = make_resolve_document_handler(conn)
        result = asyncio.get_event_loop().run_until_complete(
            handler({"reference_type": "work_order", "reference_id": "WO-42"})
        )
        data = json.loads(result["content"][0]["text"])
        assert data["resolved"] is True
        assert data["source_position"] is not None
        assert data["target_position"] is not None

    def test_subscribe_affordances(self) -> None:
        wm = WorldModel()
        wm.register_entity("panda_a")
        phyte = Phyte(
            semantic_id="joint_0",
            frame="world",
            pose=identity_se3(),
            timestamp=0.0,
            unit="rad",
            value=np.array([0.0]),
            covariance=np.array([[1e-4]]),
        )
        wm.write("panda_a", {"joint_0": phyte})

        handler = make_subscribe_affordances_handler(wm)
        result = asyncio.get_event_loop().run_until_complete(handler({"entity_id": "panda_a"}))
        data = json.loads(result["content"][0]["text"])
        assert "movable" in data["affordances"]


@pytest.mark.unit
class TestDocumentAnchoring:
    """Test the full document → physical resolution pipeline."""

    def test_work_order_to_physical_positions(self) -> None:
        """WO-42: resolve source (Bin C) and target (QA tray) to positions."""
        conn = init_db()
        wo = query_work_order(conn, "WO-42")
        assert wo is not None

        source_pos = resolve_bin_to_position(conn, str(wo["source_bin"]))
        target_pos = resolve_bin_to_position(conn, str(wo["target_location"]))

        assert source_pos is not None
        assert target_pos is not None
        # Bin C is to the right, QA tray is further back
        assert source_pos[1] > 0  # Bin C y > 0
        assert target_pos[0] > source_pos[0]  # QA tray is further in x

    def test_cross_cutting_task_data_flow(self) -> None:
        """Verify the cross-cutting task data flows through all three paths."""
        conn = init_db()

        # Step 1: Document anchoring — resolve work order
        wo = query_work_order(conn, "WO-42")
        assert wo is not None
        source_pos = resolve_bin_to_position(conn, str(wo["source_bin"]))
        target_pos = resolve_bin_to_position(conn, str(wo["target_location"]))
        assert source_pos is not None
        assert target_pos is not None

        # Step 2: A2R — would command robot to go to source_pos
        # (verified by the MCP handler test above)

        # Step 3: R2R — would translate between robots
        adapter_a = PandaAdapter(entity_id="panda_a")
        adapter_b = PandaAdapter(entity_id="panda_b")
        from psl.ir.translation import translate_r2r
        from sim.wrapper import MuJoCoSim

        sim = MuJoCoSim(seed=42)
        sim.step(100)
        jpos = sim.get_joint_positions()
        jvel = sim.get_joint_velocities()
        ee_pos, ee_quat = sim.get_ee_pose()
        state_a: dict[str, object] = {
            "joint_positions": jpos,
            "joint_velocities": jvel,
            "ee_position": ee_pos,
            "ee_quaternion": ee_quat,
            "time": sim.time,
        }

        # R2R: A → IR → B
        state_b = translate_r2r(adapter_a, adapter_b, state_a)
        np.testing.assert_allclose(
            np.asarray(state_b["joint_positions"]),
            np.asarray(state_a["joint_positions"]),
            atol=1e-12,
        )
