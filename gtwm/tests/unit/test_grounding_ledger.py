from datetime import UTC, datetime
from pathlib import Path

import pytest

from gtwm.grounding.ledger import DiscrepancyEntry, DiscrepancyLedger

pytestmark = pytest.mark.unit


def _entry(discrepancy_id: str = "d1") -> DiscrepancyEntry:
    now = datetime.now(UTC)
    return DiscrepancyEntry(
        discrepancy_id=discrepancy_id,
        object_id="gt:Pallet_0001",
        physical_value="gt:Zone_Storage_A",
        physical_confidence=0.9,
        physical_source="wm",
        physical_valid_time=now,
        record_value="gt:Zone_Storage_B",
        record_source_system="wms_mock",
        record_registration_time=now,
        discrepancy_type="wrong_slot",
        severity="medium",
        detected_at=now,
        epsilon_at_detection=0.4,
    )


def test_create_and_confirm_and_resolve(tmp_path: Path) -> None:
    ledger = DiscrepancyLedger(tmp_path / "ledger.sqlite")
    ledger.create(_entry())
    assert ledger.get("d1")["status"] == "open"

    ledger.confirm("d1", resolver="operator:alice")
    assert ledger.get("d1")["status"] == "confirmed"

    ledger.resolve("d1", resolver="operator:alice", resolution="現物確認により訂正")
    entry = ledger.get("d1")
    assert entry["status"] == "resolved"
    assert entry["resolution"] == "現物確認により訂正"
    ledger.close()


def test_dismiss_is_terminal(tmp_path: Path) -> None:
    ledger = DiscrepancyLedger(tmp_path / "ledger.sqlite")
    ledger.create(_entry())
    ledger.dismiss("d1", resolver="operator:bob")
    assert ledger.get("d1")["status"] == "dismissed"
    with pytest.raises(ValueError):
        ledger.confirm("d1", resolver="operator:bob")
    ledger.close()


def test_resolve_requires_confirmed_first(tmp_path: Path) -> None:
    ledger = DiscrepancyLedger(tmp_path / "ledger.sqlite")
    ledger.create(_entry())
    with pytest.raises(ValueError):
        ledger.resolve("d1", resolver="operator:bob", resolution="x")
    ledger.close()


def test_create_requires_open_status(tmp_path: Path) -> None:
    ledger = DiscrepancyLedger(tmp_path / "ledger.sqlite")
    entry = _entry()
    entry.status = "confirmed"
    with pytest.raises(ValueError):
        ledger.create(entry)
    ledger.close()


def test_list_by_status(tmp_path: Path) -> None:
    ledger = DiscrepancyLedger(tmp_path / "ledger.sqlite")
    ledger.create(_entry("d1"))
    ledger.create(_entry("d2"))
    ledger.confirm("d2", resolver="operator:alice")
    open_entries = ledger.list_by_status("open")
    confirmed_entries = ledger.list_by_status("confirmed")
    assert {e["discrepancy_id"] for e in open_entries} == {"d1"}
    assert {e["discrepancy_id"] for e in confirmed_entries} == {"d2"}
    ledger.close()
