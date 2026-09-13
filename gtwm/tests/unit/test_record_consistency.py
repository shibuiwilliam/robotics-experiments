"""`grounding/record_consistency.py`（記録 vs 物理の突き合わせ）のユニットテスト。"""

from __future__ import annotations

import pandas as pd
import pytest

from gtwm.grounding.record_consistency import detect_record_discrepancies

pytestmark = pytest.mark.unit


def _poses_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"t": 10.0, "entity_gt_id": "gt:Pallet_0001", "zone": "Storage_A"},
            {"t": 11.0, "entity_gt_id": "gt:Pallet_0001", "zone": "Storage_A"},
        ]
    )


def _base_record(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_type": "record",
        "entity_gt_id": "gt:Pallet_0001",
        "zone": "Storage_A",
        "biz_step": "urn:epcglobal:cbv:bizstep:storing",
        "t_true": 10.0,
        "t_obs": 10.0,
    }
    row.update(overrides)
    return row


def _anchor(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_type": "anchor",
        "entity_gt_id": "gt:Pallet_0001",
        "zone": "Storage_A",
        "biz_step": None,
        "t_true": 10.0,
        "t_obs": 10.0,
    }
    row.update(overrides)
    return row


def test_normal_record_produces_no_candidates() -> None:
    events = pd.DataFrame([_base_record(), _anchor()])
    candidates = detect_record_discrepancies(events, _poses_df())
    assert candidates == []


def test_late_registration_detected_above_threshold() -> None:
    events = pd.DataFrame([_base_record(t_obs=30.0), _anchor()])
    candidates = detect_record_discrepancies(
        events, _poses_df(), late_registration_threshold_s=10.0
    )
    types = [c.discrepancy_type for c in candidates]
    assert "late_registration" in types


def test_wrong_slot_detected_when_zone_mismatches_physical() -> None:
    events = pd.DataFrame([_base_record(zone="Pick"), _anchor()])
    candidates = detect_record_discrepancies(events, _poses_df())
    types = [c.discrepancy_type for c in candidates]
    assert "wrong_slot" in types


def test_ghost_stock_detected_when_no_anchor_nearby() -> None:
    events = pd.DataFrame([_base_record()])  # アンカー無し
    candidates = detect_record_discrepancies(events, _poses_df(), ghost_window_s=5.0)
    types = [c.discrepancy_type for c in candidates]
    assert "ghost_stock" in types


def test_anchor_type_rows_are_not_treated_as_records() -> None:
    events = pd.DataFrame([_anchor()])  # record が無い
    candidates = detect_record_discrepancies(events, _poses_df())
    assert candidates == []
