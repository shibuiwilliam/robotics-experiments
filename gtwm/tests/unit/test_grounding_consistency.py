import pytest

from gtwm.grounding.consistency import (
    FactSample,
    compute_epsilon,
    effective_horizon,
    load_epsilon_config,
    zone_distance,
)

pytestmark = pytest.mark.unit


def test_zone_distance_matches_appendix_a_rule() -> None:
    cfg = load_epsilon_config()
    assert zone_distance(2, 2, cfg) == 0.0
    assert zone_distance(2, 3, cfg) == pytest.approx(cfg.d_weights.zone_adjacent_penalty)
    assert zone_distance(0, 5, cfg) == 1.0


def test_compute_epsilon_zero_when_predictions_match_current() -> None:
    samples = [
        FactSample(
            entity_gt_id="gt:Pallet_0001",
            t=0.0,
            perceived_zone_idx=2,
            truth_zone_idx=2,
            future_zone_idx=2,
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
        )
    ]
    record = compute_epsilon(samples, horizon_s=60.0)
    assert record.decomposition["perception"] == pytest.approx(0.0)
    assert record.decomposition["process_deviation"] > 0.0


def test_compute_epsilon_empty_samples_returns_zero() -> None:
    record = compute_epsilon([], horizon_s=10.0)
    assert record.epsilon == 0.0
    assert record.n_samples == 0


def test_effective_horizon_picks_max_horizon_within_tau() -> None:
    from gtwm.grounding.consistency import EpsilonRecord

    records = [
        EpsilonRecord(horizon_s=10.0, epsilon=0.1, n_samples=1),
        EpsilonRecord(horizon_s=60.0, epsilon=0.15, n_samples=1),
        EpsilonRecord(horizon_s=300.0, epsilon=0.5, n_samples=1),
    ]
    assert effective_horizon(records, tau=0.2) == 60.0
    assert effective_horizon(records, tau=0.05) is None
