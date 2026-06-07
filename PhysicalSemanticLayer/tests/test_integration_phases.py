"""Tests for Phase B/C/D integration — agent runner, pseudo-cloud server, VLA encoder.

Phase B (agent_runner): tested offline only — @pytest.mark.api for real SDK calls.
Phase C (server): tested via ASGI test client (no actual HTTP server needed).
Phase D (VLA encoder): tested with hash-based fallback (no CLIP model needed).
"""

from __future__ import annotations

import numpy as np
import pytest

# ── Phase C: Pseudo-cloud HTTP service ──


@pytest.mark.unit
class TestPseudoCloudServer:
    """Test the pseudo-cloud HTTP API via ASGI test client."""

    def test_health_endpoint(self) -> None:
        from starlette.testclient import TestClient

        from pseudo_cloud.server import app

        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_get_workorder(self) -> None:
        from starlette.testclient import TestClient

        from pseudo_cloud.server import app

        client = TestClient(app)
        response = client.get("/api/v1/workorders/WO-42")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["work_order_id"] == "WO-42"

    def test_get_workorder_not_found(self) -> None:
        from starlette.testclient import TestClient

        from pseudo_cloud.server import app

        client = TestClient(app)
        response = client.get("/api/v1/workorders/NONEXISTENT")
        assert response.status_code == 404

    def test_get_bin(self) -> None:
        from starlette.testclient import TestClient

        from pseudo_cloud.server import app

        client = TestClient(app)
        response = client.get("/api/v1/bins/C")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["position"] == [0.3, 0.3, 0.45]

    def test_get_inventory(self) -> None:
        from starlette.testclient import TestClient

        from pseudo_cloud.server import app

        client = TestClient(app)
        response = client.get("/api/v1/inventory/gear_blue_001")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "defective"

    def test_create_exception(self) -> None:
        from starlette.testclient import TestClient

        from pseudo_cloud.server import app

        client = TestClient(app)
        response = client.post(
            "/api/v1/exceptions",
            json={"item_id": "gear_blue_001", "reason": "defective"},
        )
        assert response.status_code == 201
        assert response.json()["status"] == "created"


# ── Phase D: VLA Encoder ──


@pytest.mark.unit
class TestVLAEncoder:
    """Test VLA encoder with hash-based fallback (no CLIP needed)."""

    def test_encode_object_returns_phyte(self) -> None:
        from psl.grounding.vla_encoder import VLAEncoder

        encoder = VLAEncoder(use_real_clip=False, seed=42)
        phyte = encoder.encode_object("circuit_board", timestamp=1.0)
        assert phyte.semantic_id == "embedding:circuit_board"
        assert phyte.value.shape == (512,)
        assert len(phyte.provenance.chain) == 1
        assert "hash_fallback" in phyte.provenance.chain[0].source

    def test_encode_deterministic(self) -> None:
        from psl.grounding.vla_encoder import VLAEncoder

        enc1 = VLAEncoder(use_real_clip=False, seed=42)
        enc2 = VLAEncoder(use_real_clip=False, seed=42)
        p1 = enc1.encode_object("battery_pack")
        p2 = enc2.encode_object("battery_pack")
        np.testing.assert_allclose(p1.value, p2.value)

    def test_different_objects_different_embeddings(self) -> None:
        from psl.grounding.vla_encoder import VLAEncoder

        encoder = VLAEncoder(use_real_clip=False)
        e1 = encoder.encode_object("circuit_board")
        e2 = encoder.encode_object("battery_pack")
        assert not np.allclose(e1.value, e2.value)

    def test_predict_affordances(self) -> None:
        from psl.grounding.vla_encoder import VLAEncoder

        encoder = VLAEncoder(use_real_clip=False, seed=42)
        pred = encoder.predict_affordances("circuit_board")
        assert isinstance(pred.graspable, bool)
        assert isinstance(pred.detachable, bool)
        assert pred.material in ["pcb", "metal", "plastic", "glass", "composite"]
        assert pred.embedding.shape == (512,)
        assert pred.confidence > 0

    def test_embedding_is_unit_normalized(self) -> None:
        from psl.grounding.vla_encoder import VLAEncoder

        encoder = VLAEncoder(use_real_clip=False)
        phyte = encoder.encode_object("heat_sink")
        norm = float(np.linalg.norm(phyte.value))
        assert abs(norm - 1.0) < 1e-10


# ── Phase B: Agent runner prompts exist ──


@pytest.mark.unit
class TestAgentRunner:
    """Test that agent prompts are defined for all scenarios (no API calls)."""

    def test_all_scenario_prompts_exist(self) -> None:
        from eval.scenarios.agent_runner import SCENARIO_PROMPTS

        expected = [
            "s1_mixed_fleet_pick",
            "s2_line_changeover",
            "s3_lab_custody",
            "s4_field_inspection",
            "s5_pharma_logistics",
            "s6_ewaste_disassembly",
            "s7_degraded_ops",
        ]
        for scenario_id in expected:
            assert scenario_id in SCENARIO_PROMPTS, f"Missing prompt for {scenario_id}"
            assert len(SCENARIO_PROMPTS[scenario_id]) > 50, f"Prompt too short for {scenario_id}"
