"""Extended pseudo-cloud tests — richer data, pagination, fault injection."""

from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

from pseudo_cloud.data import check_permission, init_db, query_inventory, query_work_order
from pseudo_cloud.server import app


@pytest.mark.unit
class TestExpandedData:
    def test_inventory_has_20_plus_items(self) -> None:
        conn = init_db()
        cursor = conn.execute("SELECT COUNT(*) FROM inventory")
        count = cursor.fetchone()[0]
        assert count >= 20

    def test_work_orders_multiple_statuses(self) -> None:
        conn = init_db()
        cursor = conn.execute("SELECT DISTINCT status FROM work_orders")
        statuses = {row[0] for row in cursor.fetchall()}
        assert "pending" in statuses
        assert "completed" in statuses

    def test_bin_locations_expanded(self) -> None:
        conn = init_db()
        cursor = conn.execute("SELECT COUNT(*) FROM bin_locations")
        count = cursor.fetchone()[0]
        assert count >= 8

    def test_permissions_table(self) -> None:
        conn = init_db()
        assert check_permission(conn, "operator", "inventory", "read")
        assert check_permission(conn, "supervisor", "work_orders", "write")
        assert not check_permission(conn, "operator", "work_orders", "write")

    def test_inventory_handling_flags(self) -> None:
        conn = init_db()
        cursor = conn.execute("SELECT DISTINCT handling FROM inventory")
        flags = {row[0] for row in cursor.fetchall()}
        assert "normal" in flags
        assert "fragile" in flags
        assert "hazardous" in flags

    def test_existing_items_still_present(self) -> None:
        """Ensure original items were not removed."""
        conn = init_db()
        assert query_inventory(conn, "gear_blue_001") is not None
        assert query_inventory(conn, "gear_red_002") is not None
        assert query_inventory(conn, "bolt_003") is not None

    def test_existing_work_order_still_present(self) -> None:
        conn = init_db()
        assert query_work_order(conn, "WO-42") is not None


@pytest.mark.unit
class TestServerEndpoints:
    def test_inventory_list(self) -> None:
        client = TestClient(app)
        resp = client.get("/api/v1/inventory?limit=5&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) <= 5
        assert "total" in data

    def test_inventory_pagination(self) -> None:
        client = TestClient(app)
        r1 = client.get("/api/v1/inventory?limit=3&offset=0")
        r2 = client.get("/api/v1/inventory?limit=3&offset=3")
        assert r1.json()["items"] != r2.json()["items"]

    def test_create_work_order_valid(self) -> None:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/workorders",
            json={
                "item_id": "GEAR-001",
                "description": "Test order",
                "source_bin": "Bin_A",
                "target_bin": "QA_TRAY",
            },
        )
        assert resp.status_code == 201

    def test_create_work_order_invalid_item(self) -> None:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/workorders",
            json={
                "item_id": "NONEXISTENT",
                "description": "Bad order",
                "source_bin": "Bin_A",
                "target_bin": "QA_TRAY",
            },
        )
        assert resp.status_code == 400

    def test_fault_injection_error(self) -> None:
        client = TestClient(app)
        resp = client.get("/health", headers={"X-Inject-Error": "500"})
        assert resp.status_code == 500

    def test_fault_injection_delay(self) -> None:
        client = TestClient(app)
        start = time.time()
        resp = client.get("/health", headers={"X-Inject-Delay-Ms": "100"})
        elapsed = time.time() - start
        assert resp.status_code == 200
        assert elapsed >= 0.08  # Allow some timing slack

    def test_existing_single_item_endpoint(self) -> None:
        """Ensure the original /api/v1/inventory/{item_id} still works."""
        client = TestClient(app)
        resp = client.get("/api/v1/inventory/gear_blue_001")
        assert resp.status_code == 200
        assert resp.json()["data"]["item_id"] == "gear_blue_001"

    def test_existing_workorder_endpoint(self) -> None:
        """Ensure the original /api/v1/workorders/{wo_id} still works."""
        client = TestClient(app)
        resp = client.get("/api/v1/workorders/WO-42")
        assert resp.status_code == 200
        assert resp.json()["data"]["work_order_id"] == "WO-42"
