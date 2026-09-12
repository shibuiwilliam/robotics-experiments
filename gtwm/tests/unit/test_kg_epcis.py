"""wms_mock の events.parquet 形式（record 行）から Belief への変換（kg/epcis.py）。"""

from datetime import timedelta

import pandas as pd
import pytest

from gtwm.kg.epcis import episode_time_to_datetime, events_to_beliefs, zone_to_gt_id

pytestmark = pytest.mark.unit


def _events_df() -> pd.DataFrame:
    # Case_0001 が Storage_A → Pick と移動し、Case_0002 は Storage_B のみ。
    # anchor 行は無視されることを確認するために1行混ぜる。
    return pd.DataFrame(
        [
            {
                "event_type": "anchor",
                "entity": "case:1",
                "entity_gt_id": "gt:Case_0001",
                "anchor_type": "scan",
                "biz_step": None,
                "disposition": None,
                "zone": "Storage_A",
                "detail": "{}",
                "t_true": 0.5,
                "t_obs": 0.5,
            },
            {
                "event_type": "record",
                "entity": "case:1",
                "entity_gt_id": "gt:Case_0001",
                "anchor_type": None,
                "biz_step": "urn:epcglobal:cbv:bizstep:storing",
                "disposition": "urn:epcglobal:cbv:disp:active",
                "zone": "Storage_A",
                "detail": "{}",
                "t_true": 1.0,
                "t_obs": 1.0,
            },
            {
                "event_type": "record",
                "entity": "case:1",
                "entity_gt_id": "gt:Case_0001",
                "anchor_type": None,
                "biz_step": "urn:epcglobal:cbv:bizstep:picking",
                "disposition": "urn:epcglobal:cbv:disp:active",
                "zone": "Pick",
                "detail": "{}",
                "t_true": 5.0,
                "t_obs": 5.2,
            },
            {
                "event_type": "record",
                "entity": "case:2",
                "entity_gt_id": "gt:Case_0002",
                "anchor_type": None,
                "biz_step": "urn:epcglobal:cbv:bizstep:storing",
                "disposition": "urn:epcglobal:cbv:disp:active",
                "zone": "Storage_B",
                "detail": "{}",
                "t_true": 2.0,
                "t_obs": 2.0,
            },
        ]
    )


def test_events_to_beliefs_ignores_anchor_rows() -> None:
    beliefs = events_to_beliefs(_events_df())
    assert len(beliefs) == 3  # 3 record 行のみ（anchor 行は除外）


def test_events_to_beliefs_maps_zone_and_time() -> None:
    beliefs = events_to_beliefs(_events_df())
    case1_first = next(
        b for b in beliefs if b.subject == "gt:Case_0001" and b.object == zone_to_gt_id("Storage_A")
    )
    assert case1_first.source == "record"
    assert case1_first.confidence == pytest.approx(1.0)
    assert case1_first.valid_from == episode_time_to_datetime(1.0)
    assert case1_first.transaction_time == episode_time_to_datetime(1.0)


def test_events_to_beliefs_closes_previous_interval_on_move() -> None:
    """同一個体が移動したら、直前の信念の valid_to が新しい信念の valid_from に閉じられる。"""
    beliefs = events_to_beliefs(_events_df())
    case1_beliefs = [b for b in beliefs if b.subject == "gt:Case_0001"]
    assert len(case1_beliefs) == 2
    first, second = case1_beliefs
    assert first.object == zone_to_gt_id("Storage_A")
    assert second.object == zone_to_gt_id("Pick")
    assert first.valid_to == second.valid_from == episode_time_to_datetime(5.0)
    assert second.valid_to is None  # 最新の信念はまだ「現在も有効」


def test_events_to_beliefs_does_not_cross_contaminate_subjects() -> None:
    beliefs = events_to_beliefs(_events_df())
    case2_beliefs = [b for b in beliefs if b.subject == "gt:Case_0002"]
    assert len(case2_beliefs) == 1
    assert case2_beliefs[0].valid_to is None


def test_episode_time_to_datetime_is_monotonic() -> None:
    assert episode_time_to_datetime(5.0) - episode_time_to_datetime(1.0) == timedelta(seconds=4)
