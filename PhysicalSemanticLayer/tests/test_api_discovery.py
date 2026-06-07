"""Tests for pseudo-cloud discovery APIs and request stats."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from pseudo_cloud.server import app, get_request_stats, reset_request_stats


@pytest.mark.unit
class TestDiscoveryAPIs:
    def test_list_bins(self) -> None:
        client = TestClient(app)
        resp = client.get("/api/v1/bins")
        assert resp.status_code == 200
        data = resp.json()
        assert "bins" in data
        assert len(data["bins"]) >= 8
        # Each bin has bin_id, position, frame
        for b in data["bins"]:
            assert "bin_id" in b
            assert "position" in b
            assert len(b["position"]) == 3

    def test_list_workorders(self) -> None:
        client = TestClient(app)
        resp = client.get("/api/v1/workorders")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 5

    def test_list_workorders_pagination(self) -> None:
        client = TestClient(app)
        r1 = client.get("/api/v1/workorders?limit=2&offset=0")
        r2 = client.get("/api/v1/workorders?limit=2&offset=2")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert len(r1.json()["items"]) <= 2

    def test_list_sops(self) -> None:
        client = TestClient(app)
        resp = client.get("/api/v1/sops")
        assert resp.status_code == 200
        data = resp.json()
        assert "sops" in data
        assert len(data["sops"]) >= 1
        for s in data["sops"]:
            assert "sop_id" in s
            assert "title" in s

    def test_existing_single_endpoints_still_work(self) -> None:
        """Verify parameterized endpoints still work after route reorder."""
        client = TestClient(app)
        assert client.get("/api/v1/bins/Bin_A").status_code == 200
        assert client.get("/api/v1/workorders/WO-42").status_code == 200
        assert client.get("/api/v1/inventory/GEAR-001").status_code == 200


@pytest.mark.unit
class TestRequestStats:
    def test_stats_tracking(self) -> None:
        reset_request_stats()
        client = TestClient(app)
        client.get("/api/v1/bins/NONEXISTENT_BIN")
        client.get("/api/v1/bins/Bin_A")
        stats = get_request_stats()
        assert stats["total"] >= 2
        assert stats["404"] >= 1

    def test_reset_stats(self) -> None:
        reset_request_stats()
        stats = get_request_stats()
        assert stats["total"] == 0
        assert stats["404"] == 0


@pytest.mark.unit
class TestOrchestratorFields:
    def test_orchestrator_result_has_new_fields(self) -> None:
        import dataclasses

        from eval.scenarios.orchestrator import OrchestratorResult

        fields = {f.name for f in dataclasses.fields(OrchestratorResult)}
        assert "agent_fallback_used" in fields
        assert "agent_404_count" in fields
