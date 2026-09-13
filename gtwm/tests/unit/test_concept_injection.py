"""EXP-08 向け概念注入（`sim/concept_injection.py`）のユニットテスト。"""

from __future__ import annotations

import pandas as pd
import pytest

from gtwm.sim.concept_injection import (
    CBV_BIZSTEP_REINSPECTING,
    ConceptInjectionConfig,
    apply_concept_injections,
)

pytestmark = pytest.mark.unit


def _poses_df() -> pd.DataFrame:
    rows = []
    # case:2（上段積み＝oversized_cargo_proxy対象）
    for t in [0.0, 1.0, 2.0]:
        rows.append(
            {"t": t, "entity": "case:2", "entity_gt_id": "gt:Case_0002", "zone": "Storage_A"}
        )
    # case:21（単体＝対象外）
    for t in [0.0, 1.0, 2.0]:
        rows.append(
            {"t": t, "entity": "case:21", "entity_gt_id": "gt:Case_0021", "zone": "Storage_A"}
        )
    # pallet:1（Dock_Outに長時間滞留＝staging_overflow対象）
    for t in [0.0, 1.0, 2.0, 3.0]:
        rows.append(
            {"t": t, "entity": "pallet:1", "entity_gt_id": "gt:Pallet_0001", "zone": "Dock_Out"}
        )
    return pd.DataFrame(rows)


def _events_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "event_type": "record",
                "entity": "case:2",
                "entity_gt_id": "gt:Case_0002",
                "biz_step": "urn:epcglobal:cbv:bizstep:storing",
                "zone": "Storage_A",
                "t_true": 1.0,
                "t_obs": 1.0,
            }
        ]
    )


def test_no_injections_when_all_disabled() -> None:
    events_out, ledger = apply_concept_injections(
        _events_df(), _poses_df(), ConceptInjectionConfig(), injection_seed=0
    )
    assert ledger.empty
    assert len(events_out) == len(_events_df())


def test_oversized_cargo_tags_only_top_tier_stacked_cases() -> None:
    _, ledger = apply_concept_injections(
        _events_df(),
        _poses_df(),
        ConceptInjectionConfig(enable_oversized_cargo_tagging=True),
        injection_seed=0,
    )
    rows = ledger[ledger["injection_type"] == "oversized_cargo_proxy"]
    assert list(rows["entity"]) == ["gt:Case_0002"]


def test_staging_overflow_tags_high_dwell_entity() -> None:
    _, ledger = apply_concept_injections(
        _events_df(),
        _poses_df(),
        ConceptInjectionConfig(
            enable_staging_overflow_tagging=True, staging_dwell_fraction_threshold=0.5
        ),
        injection_seed=0,
    )
    rows = ledger[ledger["injection_type"] == "staging_overflow"]
    assert list(rows["entity"]) == ["gt:Pallet_0001"]


def test_reinspection_adds_new_record_event_with_unregistered_bizstep() -> None:
    events_out, ledger = apply_concept_injections(
        _events_df(),
        _poses_df(),
        ConceptInjectionConfig(enable_reinspection=True, reinspection_prob=1.0),
        injection_seed=0,
    )
    rows = ledger[ledger["injection_type"] == "reinspecting_step"]
    assert len(rows) == 1
    assert (events_out["biz_step"] == CBV_BIZSTEP_REINSPECTING).sum() == 1


def test_reinspection_probability_zero_adds_nothing() -> None:
    events_out, ledger = apply_concept_injections(
        _events_df(),
        _poses_df(),
        ConceptInjectionConfig(enable_reinspection=True, reinspection_prob=0.0),
        injection_seed=0,
    )
    assert ledger.empty
    assert len(events_out) == len(_events_df())
