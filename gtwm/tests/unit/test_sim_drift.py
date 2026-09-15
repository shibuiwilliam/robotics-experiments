"""`sim/drift.py`（業務手順変更：検品位置変更・ピッキング順序変更）のユニットテスト。"""

from __future__ import annotations

import pandas as pd
import pytest

from gtwm.sim.drift import DriftConfig, apply_drift, drift_change_report
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


def _realistic_events_df() -> pd.DataFrame:
    """実際に生成されるエピソードと同じ構成（`inspecting` が1件も無い）。

    `data/sim/exp04_seed0/ep_0000_seed0/events.parquet`（343行）の実測では
    biz_step は storing / picking のみで inspecting は0件だった（2026-09-14）。
    """
    rows = [
        {
            "event_type": "record",
            "entity": f"pallet:{i}",
            "entity_gt_id": f"gt:Pallet_{i:04d}",
            "biz_step": CBV_BIZSTEP["storing"],
            "zone": "Storage_A",
            "t_true": float(i),
            "t_obs": float(i),
        }
        for i in range(1, 6)
    ]
    rows += [
        {
            "event_type": "record",
            "entity": f"pallet:{i}",
            "entity_gt_id": f"gt:Pallet_{i:04d}",
            "biz_step": CBV_BIZSTEP["picking"],
            "zone": "Pick",
            "t_true": float(10 + i),
            "t_obs": float(10 + i),
        }
        for i in range(6, 8)
    ]
    return pd.DataFrame(rows)


def test_drift_change_report_exposes_inert_inspection_change() -> None:
    """`inspecting` が存在しないデータでは検品位置変更が no-op になることを可視化する。

    EXP-04 本実行（`docs/results/EXP-04.md`）で AUROC が偶然水準になった根本原因。
    従来のテストは `inspecting` 行を含む合成フィクスチャを使っていたため通ってしまい、
    この退化を検出できなかった。
    """
    df = _realistic_events_df()
    out = apply_drift(df, DriftConfig(inspection_position_change=True))
    report = drift_change_report(df, out)

    assert report["inspecting_rows"] == 0
    assert report["zone_changed"] == 0, (
        "inspecting が無いデータでは検品位置変更は0行しか変えない（no-op）。"
        "実験でドリフトを注入したつもりになる退行を防ぐため、この事実を指標として可視化する。"
    )


def test_drift_change_report_counts_active_picking_change() -> None:
    df = _realistic_events_df()
    out = apply_drift(df, DriftConfig(picking_order_change=True))
    report = drift_change_report(df, out)

    assert report["picking_rows"] == 2
    assert report["entity_changed"] == 2  # 2件が入れ替わる


def test_drift_change_report_handles_empty_frame() -> None:
    empty = pd.DataFrame()
    report = drift_change_report(empty, empty)
    assert report["inspecting_rows"] == 0
    assert report["zone_changed"] == 0
