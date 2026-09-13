"""`sim/wms_mock.apply_injections`（5型の乖離注入）のユニットテスト。"""

from __future__ import annotations

import pandas as pd
import pytest

from gtwm.sim.wms_mock import CBV_BIZSTEP, CBV_DISPOSITION_ACTIVE, InjectionConfig, apply_injections

pytestmark = pytest.mark.unit


def _events_df(n: int = 20) -> pd.DataFrame:
    zones = ["Storage_A", "Storage_B", "Pick"]
    rows = []
    for i in range(n):
        rows.append(
            {
                "event_type": "record",
                "entity": f"pallet:{i}",
                "entity_gt_id": f"gt:Pallet_{i:04d}",
                "anchor_type": None,
                "biz_step": CBV_BIZSTEP["storing"] if i % 2 == 0 else CBV_BIZSTEP["picking"],
                "disposition": CBV_DISPOSITION_ACTIVE,
                "zone": zones[i % len(zones)],
                "detail": "{}",
                "t_true": float(i),
                "t_obs": float(i),
            }
        )
    return pd.DataFrame(rows)


def test_all_zero_is_noop_and_empty_ledger() -> None:
    df = _events_df()
    out_df, ledger = apply_injections(df, InjectionConfig(), injection_seed=0)
    pd.testing.assert_frame_equal(out_df, df)
    assert ledger.empty
    assert list(ledger.columns) == ["episode_id", "injection_type", "entity", "t_true", "detail"]


def test_unscanned_move_removes_rows() -> None:
    df = _events_df()
    cfg = InjectionConfig(unscanned_move=3)
    out_df, ledger = apply_injections(df, cfg, injection_seed=1)
    assert len(out_df) == len(df) - 3
    assert (ledger["injection_type"] == "unscanned_move").sum() == 3


def test_wrong_slot_changes_zone() -> None:
    df = _events_df()
    cfg = InjectionConfig(wrong_slot=2)
    out_df, ledger = apply_injections(df, cfg, injection_seed=2)
    assert len(out_df) == len(df)
    rows = ledger[ledger["injection_type"] == "wrong_slot"]
    assert len(rows) == 2
    for _, r in rows.iterrows():
        recorded_zone = out_df.loc[out_df["t_true"] == r["t_true"], "zone"].iloc[0]
        assert pd.notna(recorded_zone)
        assert r["detail"] != ""


def test_wrong_scan_swaps_entity() -> None:
    df = _events_df()
    cfg = InjectionConfig(wrong_scan=2)
    out_df, ledger = apply_injections(df, cfg, injection_seed=3)
    assert len(out_df) == len(df)
    assert (ledger["injection_type"] == "wrong_scan").sum() == 2


def test_late_registration_delays_t_obs() -> None:
    df = _events_df()
    cfg = InjectionConfig(
        late_registration=2, late_registration_delay_s_min=10.0, late_registration_delay_s_max=20.0
    )
    out_df, ledger = apply_injections(df, cfg, injection_seed=4)
    rows = ledger[ledger["injection_type"] == "late_registration"]
    assert len(rows) == 2
    for _, r in rows.iterrows():
        matching = out_df[out_df["t_true"] == r["t_true"]]
        assert (matching["t_obs"] - matching["t_true"] >= 10.0).all()


def test_ghost_stock_adds_fabricated_rows() -> None:
    df = _events_df()
    cfg = InjectionConfig(ghost_stock=3)
    out_df, ledger = apply_injections(df, cfg, injection_seed=5)
    assert len(out_df) == len(df) + 3
    assert (ledger["injection_type"] == "ghost_stock").sum() == 3
    ghost_rows = out_df[out_df["detail"].astype(str).str.contains("ghost_stock", na=False)]
    assert len(ghost_rows) == 3


def test_injection_seed_is_deterministic() -> None:
    df = _events_df()
    cfg = InjectionConfig(
        unscanned_move=2, wrong_slot=2, wrong_scan=2, late_registration=2, ghost_stock=2
    )
    out_a, ledger_a = apply_injections(df, cfg, injection_seed=42)
    out_b, ledger_b = apply_injections(df, cfg, injection_seed=42)
    pd.testing.assert_frame_equal(out_a.reset_index(drop=True), out_b.reset_index(drop=True))
    pd.testing.assert_frame_equal(ledger_a.reset_index(drop=True), ledger_b.reset_index(drop=True))


def test_ledger_entity_column_uses_gt_id_not_sim_name() -> None:
    """`eval.scoring.score_detection` は台帳の `object_id`（gt: 形式）と注入台帳の
    `entity` を突き合わせる（`DiscrepancyEntry.object_id=entity_gt_id` が既定）。
    ここで sim 名（例：`pallet:5`）を入れると検知が原理的に一切一致しなくなる。"""
    df = _events_df()
    cfg = InjectionConfig(
        unscanned_move=2, wrong_slot=2, wrong_scan=2, late_registration=2, ghost_stock=2
    )
    _, ledger = apply_injections(df, cfg, injection_seed=7)
    assert not ledger.empty
    assert ledger["entity"].str.startswith("gt:").all()


def test_candidate_scarcity_caps_gracefully() -> None:
    """候補が足りなくてもクラッシュせず、可能な数だけ注入する。"""
    df = _events_df(n=2)
    cfg = InjectionConfig(unscanned_move=10)
    out_df, ledger = apply_injections(df, cfg, injection_seed=6)
    assert len(ledger) <= 2
    assert len(out_df) == len(df) - len(ledger)
