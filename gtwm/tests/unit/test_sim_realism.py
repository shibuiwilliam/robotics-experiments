"""`sim/realism.py`（一般ノイズ：ジッタ・欠落・遅延・誤登録）のユニットテスト。"""

from __future__ import annotations

import pandas as pd
import pytest

from gtwm.sim.realism import RealismConfig, apply_observation_realism, load_realism_config

pytestmark = pytest.mark.unit


def _events_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "event_type": "anchor",
                "entity": "pallet:1",
                "entity_gt_id": "gt:Pallet_0001",
                "anchor_type": "rfid_gate",
                "biz_step": None,
                "disposition": None,
                "zone": "Dock_In",
                "detail": "{}",
                "t_true": 1.0,
                "t_obs": 1.0,
            },
            {
                "event_type": "record",
                "entity": "pallet:1",
                "entity_gt_id": "gt:Pallet_0001",
                "anchor_type": None,
                "biz_step": "urn:epcglobal:cbv:bizstep:storing",
                "disposition": "urn:epcglobal:cbv:disp:active",
                "zone": "Storage_A",
                "detail": "{}",
                "t_true": 2.0,
                "t_obs": 2.0,
            },
        ]
    )


def test_all_zero_config_is_noop() -> None:
    df = _events_df()
    out = apply_observation_realism(df, RealismConfig(), seed=0)
    pd.testing.assert_frame_equal(out, df)


def test_jitter_changes_t_obs_but_not_t_true() -> None:
    df = _events_df()
    cfg = RealismConfig(anchor_jitter_s_min=0.1, anchor_jitter_s_max=0.3)
    out = apply_observation_realism(df, cfg, seed=1)
    anchor_row = out[out["event_type"] == "anchor"].iloc[0]
    assert anchor_row["t_true"] == 1.0
    assert 0.1 <= abs(anchor_row["t_obs"] - 1.0) <= 0.3


def test_record_delay_only_shifts_t_obs_forward() -> None:
    df = _events_df()
    cfg = RealismConfig(record_delay_s_min=1.0, record_delay_s_max=2.0)
    out = apply_observation_realism(df, cfg, seed=2)
    record_row = out[out["event_type"] == "record"].iloc[0]
    assert record_row["t_obs"] >= record_row["t_true"] + 1.0


def test_high_miss_rate_drops_rows() -> None:
    df = pd.concat([_events_df()] * 20, ignore_index=True)
    cfg = RealismConfig(anchor_miss_rate=1.0)
    out = apply_observation_realism(df, cfg, seed=3)
    assert (out["event_type"] == "anchor").sum() == 0


def test_load_realism_config_from_p1_yaml() -> None:
    cfg = load_realism_config("configs/realism/p1.yaml")
    assert 0.1 <= cfg.anchor_jitter_s_min <= cfg.anchor_jitter_s_max <= 0.3
    assert cfg.injections.unscanned_move >= 1
