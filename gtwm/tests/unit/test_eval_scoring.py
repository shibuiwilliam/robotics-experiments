"""`eval/scoring.py`：盲検境界の唯一の例外モジュール。注入台帳との突き合わせを検査する。"""

from __future__ import annotations

import pandas as pd
import pytest

from gtwm.eval.scoring import load_injection_ledger, score_detection
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
