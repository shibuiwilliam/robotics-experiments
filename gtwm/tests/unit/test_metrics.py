"""`eval/metrics.py` の手計算できる小さな例（.claude/rules/experiments.md）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from gtwm.eval import metrics

pytestmark = pytest.mark.unit


def test_position_mismatch() -> None:
    assert metrics.position_mismatch("gt:Zone_A", "gt:Zone_A") == 0.0
    assert (
        metrics.position_mismatch("gt:Zone_A", "gt:Zone_B", {frozenset({"gt:Zone_A", "gt:Zone_B"})})
        == 0.5
    )
    assert metrics.position_mismatch("gt:Zone_A", "gt:Zone_C") == 1.0


def test_fact_distance_d() -> None:
    d = metrics.fact_distance_d(
        {"position": 1.0, "state": 0.0, "relations": 0.5, "type": 0.0},
        {"position": 0.4, "state": 0.3, "relations": 0.2, "type": 0.1},
    )
    assert d == pytest.approx(0.4 * 1.0 + 0.2 * 0.5)


def test_epsilon_h() -> None:
    assert metrics.epsilon_h([0.0, 0.4, 0.2]) == pytest.approx(0.2)
    with pytest.raises(ValueError):
        metrics.epsilon_h([])


def test_grounding_prf() -> None:
    # preds=[0,1,1], labels=[0,1,0]:
    # クラス0: tp=1(idx0), fp=0, fn=1(idx2) -> precision=1.0, recall=0.5
    # クラス1: tp=1(idx1), fp=1(idx2), fn=0 -> precision=0.5, recall=1.0
    result = metrics.grounding_prf([0, 1, 1], [0, 1, 0])
    assert result["accuracy"] == pytest.approx(2 / 3)
    per_class = result["per_class"]
    assert per_class[0]["precision"] == pytest.approx(1.0)
    assert per_class[0]["recall"] == pytest.approx(0.5)
    assert per_class[1]["precision"] == pytest.approx(0.5)
    assert per_class[1]["recall"] == pytest.approx(1.0)
    assert result["macro_f1"] == pytest.approx(2 / 3, abs=1e-6)


def test_id_switch_rate() -> None:
    assert metrics.id_switch_rate(n_switches=1, n_objects=2, duration_hours=0.5) == pytest.approx(
        1.0
    )
    with pytest.raises(ValueError):
        metrics.id_switch_rate(1, 0, 1.0)


def test_idf1() -> None:
    assert metrics.idf1(idtp=8, idfp=2, idfn=2) == pytest.approx(0.8)
    assert metrics.idf1(0, 0, 0) == 1.0


def test_count_id_switches() -> None:
    assert metrics.count_id_switches(["a", "a", None, "a", "b"]) == 1
    assert metrics.count_id_switches(["a", "a", "a"]) == 0
    assert metrics.count_id_switches([None, "a", "b", "b"]) == 1


def test_id_counts_from_matches() -> None:
    # トラック "t1" は3フレーム中2フレームで真値 "gt1" と、1フレームだけ誤って "gt2" と
    # マッチした -> 多数決マッピングは t1->gt1。そのフレームだけ IDFP+IDFN。
    matched = [[("t1", "gt1")], [("t1", "gt1")], [("t1", "gt2")]]
    idtp, idfp, idfn = metrics.id_counts_from_matches(matched)
    assert (idtp, idfp, idfn) == (2, 1, 1)

    # 未マッチの予測/真値も追加のIDFP/IDFNとして数える。
    idtp2, idfp2, idfn2 = metrics.id_counts_from_matches(
        [[("t1", "gt1")]], n_unmatched_preds_per_frame=[1], n_unmatched_gts_per_frame=[2]
    )
    assert (idtp2, idfp2, idfn2) == (1, 1, 2)


def test_effective_horizon() -> None:
    assert metrics.effective_horizon({10.0: 0.1, 60.0: 0.3}, tau=0.2) == 10.0
    assert metrics.effective_horizon({10.0: 0.5}, tau=0.2) is None


def test_detection_rate() -> None:
    assert metrics.detection_rate(45, 50) == pytest.approx(0.9)


def test_false_alarms_per_day() -> None:
    assert metrics.false_alarms_per_day(4, 2.0) == pytest.approx(2.0)


def test_detection_latency() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    latencies = metrics.detection_latency([t0 + timedelta(seconds=60)], [t0])
    assert latencies == [60.0]


def test_ece() -> None:
    # 1区間（0.8~1.0）に2件、平均確信度0.9、正答率0.5 -> |0.9-0.5| * (2/2) = 0.4
    value = metrics.ece([0.9, 0.9], [True, False], n_bins=10)
    assert value == pytest.approx(0.4, abs=1e-6)


def test_reconstruction_ssim_identical() -> None:
    x = np.random.default_rng(0).random((8, 8))
    assert metrics.reconstruction_ssim(x, x) == pytest.approx(1.0, abs=1e-6)


def test_reconstruction_ssim_shape_mismatch() -> None:
    with pytest.raises(ValueError):
        metrics.reconstruction_ssim(np.zeros((2, 2)), np.zeros((3, 3)))


def test_reid_top1() -> None:
    assert metrics.reid_top1([1, 2, 3], [1, 2, 4]) == pytest.approx(2 / 3)
    assert metrics.reid_top1([], []) == 0.0
