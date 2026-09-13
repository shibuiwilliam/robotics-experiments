"""`eval/scoring.py`：盲検境界の唯一の例外モジュール。注入台帳との突き合わせを検査する。"""

from __future__ import annotations

import pandas as pd
import pytest

from gtwm.eval.scoring import load_injection_ledger, score_concept_discovery, score_detection
from gtwm.grounding.probes import utc

pytestmark = pytest.mark.unit


def test_load_injection_ledger_missing_file_returns_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("gtwm.eval.scoring.repo_root", lambda: tmp_path)
    df = load_injection_ledger("does_not_exist")
    assert df.empty
    assert list(df.columns) == ["episode_id", "injection_type", "entity", "t_true", "detail"]


def test_score_detection_matches_correct_object_and_type() -> None:
    # `detected_at` は実際の `ledger.py`（`grounding/probes.utc()` 経由）と同じ、
    # episode-relative 秒を Unix epoch 起点として解釈した ISO 文字列で与える
    # （バグ修正前は `float(str(...))` がここで例外になり、時刻フィルタが無効化されていた）。
    ledger_entries = [
        {
            "discrepancy_id": "d1",
            "object_id": "gt:Pallet_0001",
            "discrepancy_type": "wrong_slot",
            "detected_at": utc(100.0).isoformat(),
        },
        {
            "discrepancy_id": "d2",
            "object_id": "gt:Pallet_0002",
            "discrepancy_type": "wrong_slot",
            "detected_at": utc(200.0).isoformat(),
        },
    ]
    injections = pd.DataFrame(
        [
            {
                "episode_id": "ep0",
                "injection_type": "wrong_slot",
                "entity": "gt:Pallet_0001",
                "t_true": 98.0,
                "detail": "",
            }
        ]
    )
    result = score_detection(ledger_entries, injections, time_tolerance_s=5.0)
    assert result.n_injected == 1
    assert result.n_detected == 1
    assert result.n_false_alarms == 1  # d2 は注入と一致しない
    assert result.matched_pairs == [("d1", "gt:Pallet_0001")]
    assert result.detected_types == ["wrong_slot"]
    assert result.latencies_s == pytest.approx([2.0])  # |100.0 - 98.0|


def test_score_detection_rejects_match_outside_time_tolerance() -> None:
    """対象・型が一致しても、検知時刻が注入時刻から離れすぎていれば誤報扱いになる。"""
    ledger_entries = [
        {
            "discrepancy_id": "d1",
            "object_id": "gt:Pallet_0001",
            "discrepancy_type": "wrong_slot",
            "detected_at": utc(200.0).isoformat(),
        }
    ]
    injections = pd.DataFrame(
        [
            {
                "episode_id": "ep0",
                "injection_type": "wrong_slot",
                "entity": "gt:Pallet_0001",
                "t_true": 10.0,
                "detail": "",
            }
        ]
    )
    result = score_detection(ledger_entries, injections, time_tolerance_s=5.0)
    assert result.n_detected == 0
    assert result.n_false_alarms == 1


def test_score_detection_rejects_wrong_type() -> None:
    ledger_entries = [
        {
            "discrepancy_id": "d1",
            "object_id": "gt:Pallet_0001",
            "discrepancy_type": "ghost_stock",
            "detected_at": utc(100.0).isoformat(),
        }
    ]
    injections = pd.DataFrame(
        [
            {
                "episode_id": "ep0",
                "injection_type": "wrong_slot",
                "entity": "gt:Pallet_0001",
                "t_true": 100.0,
                "detail": "",
            }
        ]
    )
    result = score_detection(ledger_entries, injections)
    assert result.n_detected == 0
    assert result.n_false_alarms == 1


def test_score_detection_no_injections_all_false_alarms() -> None:
    ledger_entries = [
        {
            "discrepancy_id": "d1",
            "object_id": "gt:Pallet_0001",
            "discrepancy_type": "wrong_slot",
            "detected_at": utc(100.0).isoformat(),
        }
    ]
    empty = pd.DataFrame(columns=["episode_id", "injection_type", "entity", "t_true", "detail"])
    result = score_detection(ledger_entries, empty)
    assert result.n_injected == 0
    assert result.n_detected == 0
    assert result.n_false_alarms == 1


class _FakeCandidate:
    def __init__(self, member_episode_frames: list[tuple[str, int]]) -> None:
        self.member_episode_frames = member_episode_frames


def test_score_concept_discovery_hits_type_within_time_tolerance(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("gtwm.eval.scoring.repo_root", lambda: tmp_path)
    ledger_dir = tmp_path / "data" / "injections"
    ledger_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "episode_id": "ep0",
                "injection_type": "oversized_cargo_proxy",
                "entity": "gt:Case_0002",
                "t_true": 10.0,
                "detail": "{}",
            }
        ]
    ).to_parquet(ledger_dir / "ep0.parquet", index=False)

    # frame_idx=100, LOG_HZ=10 -> t=10.0s、注入時刻とちょうど一致。
    candidate = _FakeCandidate(member_episode_frames=[("ep0", 100)])
    result = score_concept_discovery([candidate], episode_ids=["ep0"], time_tolerance_s=1.0)
    assert result.hit_types == ["oversized_cargo_proxy"]
    assert result.injected_types == [
        "oversized_cargo_proxy",
        "reinspecting_step",
        "staging_overflow",
    ]


def test_score_concept_discovery_no_hit_outside_time_tolerance(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("gtwm.eval.scoring.repo_root", lambda: tmp_path)
    ledger_dir = tmp_path / "data" / "injections"
    ledger_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "episode_id": "ep0",
                "injection_type": "staging_overflow",
                "entity": "gt:Pallet_0001",
                "t_true": 100.0,
                "detail": "{}",
            }
        ]
    ).to_parquet(ledger_dir / "ep0.parquet", index=False)

    candidate = _FakeCandidate(member_episode_frames=[("ep0", 0)])  # t=0.0s、100sから遠い
    result = score_concept_discovery([candidate], episode_ids=["ep0"], time_tolerance_s=1.0)
    assert result.hit_types == []
