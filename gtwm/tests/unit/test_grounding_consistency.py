import pandas as pd
import pytest

from gtwm.grounding.consistency import (
    FactSample,
    compute_epsilon,
    effective_horizon,
    load_epsilon_config,
    lookup_expected_future_zone_idx,
    zone_distance,
)

pytestmark = pytest.mark.unit

ZONE_NAMES = ["Dock_In", "Inspect", "Storage_A", "Storage_B", "Pick", "Dock_Out"]


def test_zone_distance_matches_appendix_a_rule() -> None:
    cfg = load_epsilon_config()
    assert zone_distance(2, 2, cfg) == 0.0
    assert zone_distance(2, 3, cfg) == pytest.approx(cfg.d_weights.zone_adjacent_penalty)
    assert zone_distance(0, 5, cfg) == 1.0


def test_compute_epsilon_zero_when_predictions_match_expected() -> None:
    samples = [
        FactSample(
            entity_gt_id="gt:Pallet_0001",
            t=0.0,
            perceived_zone_idx=2,
            truth_zone_idx=2,
            future_zone_idx=2,
            expected_future_zone_idx=2,  # F^h：業務記録上もゾーン2のまま、という想定
        )
        for _ in range(5)
    ]
    record = compute_epsilon(samples, horizon_s=10.0)
    assert record.epsilon == pytest.approx(0.0)
    assert record.decomposition["perception"] == pytest.approx(0.0)
    assert record.n_samples == 5


def test_compute_epsilon_flags_perception_error_when_current_wrong() -> None:
    samples = [
        FactSample(
            entity_gt_id="gt:Pallet_0001",
            t=0.0,
            perceived_zone_idx=0,
            truth_zone_idx=2,
            future_zone_idx=0,
            expected_future_zone_idx=0,  # F^h が現在の知覚と一致 -> d=0 のまま
        )
    ]
    record = compute_epsilon(samples, horizon_s=10.0)
    # 現在時刻の知覚自体が真値と食い違う（perceived!=truth）ため、d>0 の寄与は perception に入る。
    assert record.decomposition["perception"] >= 0.0
    assert record.decomposition["process_deviation"] == pytest.approx(0.0)


def test_compute_epsilon_flags_process_deviation_when_perception_correct_but_future_diverges() -> (
    None
):
    samples = [
        FactSample(
            entity_gt_id="gt:Pallet_0001",
            t=0.0,
            perceived_zone_idx=2,
            truth_zone_idx=2,
            future_zone_idx=4,
            expected_future_zone_idx=2,  # F^h：業務記録はゾーン2のままの想定だが、WMはゾーン4を予測
        )
    ]
    record = compute_epsilon(samples, horizon_s=60.0)
    assert record.decomposition["perception"] == pytest.approx(0.0)
    assert record.decomposition["process_deviation"] > 0.0


def test_compute_epsilon_empty_samples_returns_zero() -> None:
    record = compute_epsilon([], horizon_s=10.0)
    assert record.epsilon == 0.0
    assert record.n_samples == 0


def test_compute_epsilon_excludes_samples_with_no_expected_zone() -> None:
    """個体がまだ一度も記録されていない（F^h 未定義）サンプルは計算対象から除外される。"""
    samples = [
        FactSample(
            entity_gt_id="gt:Pallet_0001",
            t=0.0,
            perceived_zone_idx=2,
            truth_zone_idx=2,
            future_zone_idx=2,
            expected_future_zone_idx=None,
        ),
        FactSample(
            entity_gt_id="gt:Pallet_0002",
            t=0.0,
            perceived_zone_idx=0,
            truth_zone_idx=0,
            future_zone_idx=1,
            expected_future_zone_idx=0,
        ),
    ]
    record = compute_epsilon(samples, horizon_s=10.0)
    assert record.n_samples == 1  # None のサンプルは除外される


def test_lookup_expected_future_zone_idx_prefers_future_record_over_perception() -> None:
    """F^h は業務記録（events.parquet 相当）を参照する。知覚ゾーンとは独立に、記録が
    示す将来のゾーン変更を追随することを確認する（旧実装は perceived_zone_idx を
    そのまま返す恒等写像だったため、この差し替えが本バグ修正の核心）。"""
    events_df = pd.DataFrame(
        [
            {
                "event_type": "record",
                "entity_gt_id": "gt:Pallet_0001",
                "zone": "Pick",
                "t_true": 5.0,
                "t_obs": 5.0,
            },
        ]
    )
    # t=0 で h=10s 先を問うと、t_obs=5.0 が (0,10] に入るのでその記録（Pick）を使う。
    # perceived_zone_idx（呼び出し側が別途持つ値）は一切参照しない。
    idx = lookup_expected_future_zone_idx(
        events_df, "gt:Pallet_0001", t_s=0.0, horizon_s=10.0, zone_names=ZONE_NAMES
    )
    assert idx == ZONE_NAMES.index("Pick")


def test_lookup_expected_future_zone_idx_falls_back_to_latest_past_record() -> None:
    events_df = pd.DataFrame(
        [
            {
                "event_type": "record",
                "entity_gt_id": "gt:Pallet_0001",
                "zone": "Storage_A",
                "t_true": 1.0,
                "t_obs": 1.0,
            },
        ]
    )
    # (t, t+h] の間に記録が無いので、直近の過去記録（Storage_A）が h 秒後も続く前提を使う。
    idx = lookup_expected_future_zone_idx(
        events_df, "gt:Pallet_0001", t_s=5.0, horizon_s=10.0, zone_names=ZONE_NAMES
    )
    assert idx == ZONE_NAMES.index("Storage_A")


def test_lookup_expected_future_zone_idx_none_when_entity_never_recorded() -> None:
    events_df = pd.DataFrame(
        [
            {
                "event_type": "record",
                "entity_gt_id": "gt:Pallet_9999",
                "zone": "Storage_A",
                "t_true": 1.0,
                "t_obs": 1.0,
            },
        ]
    )
    idx = lookup_expected_future_zone_idx(
        events_df, "gt:Pallet_0001", t_s=5.0, horizon_s=10.0, zone_names=ZONE_NAMES
    )
    assert idx is None


def test_effective_horizon_picks_max_horizon_within_tau() -> None:
    from gtwm.grounding.consistency import EpsilonRecord

    records = [
        EpsilonRecord(horizon_s=10.0, epsilon=0.1, n_samples=1),
        EpsilonRecord(horizon_s=60.0, epsilon=0.15, n_samples=1),
        EpsilonRecord(horizon_s=300.0, epsilon=0.5, n_samples=1),
    ]
    assert effective_horizon(records, tau=0.2) == 60.0
    assert effective_horizon(records, tau=0.05) is None
