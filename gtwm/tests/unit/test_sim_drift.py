"""`sim/drift.py`（業務手順変更：検品位置変更・ピッキング順序変更）のユニットテスト。"""

from __future__ import annotations

import pandas as pd
import pytest

from gtwm.sim.drift import DriftConfig, apply_drift
from gtwm.sim.wms_mock import CBV_BIZSTEP

pytestmark = pytest.mark.unit


def _events_df() -> pd.DataFrame:
    rows = [
        {
            "event_type": "record",
            "entity": "pallet:1",
            "entity_gt_id": "gt:Pallet_0001",
            "biz_step": CBV_BIZSTEP["inspecting"],
            "zone": "Inspect",
            "t_true": 1.0,
            "t_obs": 1.0,
        },
        {
            "event_type": "record",
            "entity": "pallet:2",
            "entity_gt_id": "gt:Pallet_0002",
            "biz_step": CBV_BIZSTEP["picking"],
            "zone": None,
            "t_true": 2.0,
            "t_obs": 2.0,
        },
        {
            "event_type": "record",
            "entity": "pallet:3",
            "entity_gt_id": "gt:Pallet_0003",
            "biz_step": CBV_BIZSTEP["picking"],
            "zone": None,
            "t_true": 3.0,
            "t_obs": 3.0,
        },
    ]
    return pd.DataFrame(rows)


def test_noop_when_both_disabled() -> None:
    df = _events_df()
    out = apply_drift(df, DriftConfig())
    pd.testing.assert_frame_equal(out, df)


def test_inspection_position_change_rewrites_zone() -> None:
    df = _events_df()
    out = apply_drift(df, DriftConfig(inspection_position_change=True, inspection_new_zone="Pick"))
    row = out[out["biz_step"] == CBV_BIZSTEP["inspecting"]].iloc[0]
    assert row["zone"] == "Pick"


def test_picking_order_change_reverses_entity_assignment() -> None:
    df = _events_df()
    out = apply_drift(df, DriftConfig(picking_order_change=True))
    picking = out[out["biz_step"] == CBV_BIZSTEP["picking"]].sort_values("t_true")
    assert picking["entity"].tolist() == ["pallet:3", "pallet:2"]
