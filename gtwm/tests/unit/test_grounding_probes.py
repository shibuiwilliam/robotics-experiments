from datetime import UTC, datetime

import pytest
import torch

from gtwm.grounding.probes import (
    N_ZONES,
    Probe,
    SlotFacts,
    calibrate_temperature,
    expected_calibration_error,
    slot_facts_to_beliefs,
)

pytestmark = pytest.mark.unit


def test_probe_forward_shapes() -> None:
    probe = Probe(slot_dim=16)
    slots = torch.randn(2, 3, 16)  # [B,K,D] 相当（B=2,K=3,D=16）
    out = probe(slots)
    assert out["existence_logit"].shape == (2, 3)
    assert out["zone_logit"].shape == (2, 3, N_ZONES)
    assert out["floor_xy"].shape == (2, 3, 2)


def test_zone_probs_calibration_changes_distribution() -> None:
    probe = Probe(slot_dim=8)
    slots = torch.randn(4, 8)
    probe.log_temperature.data.fill_(torch.log(torch.tensor(2.0)))
    calibrated = probe.zone_probs(slots, calibrated=True)
    uncalibrated = probe.zone_probs(slots, calibrated=False)
    assert not torch.allclose(calibrated, uncalibrated)


def test_calibrate_temperature_prefers_confident_correct_logits() -> None:
    logits = torch.tensor([[5.0, 0.0], [0.0, 5.0], [5.0, 0.0], [0.0, 5.0]])
    labels = torch.tensor([0, 1, 0, 1])
    t = calibrate_temperature(logits, labels, t_min=0.1, t_max=5.0, steps=50)
    assert 0.1 <= t <= 5.0


def test_expected_calibration_error_zero_when_perfectly_calibrated() -> None:
    # 全て確信度1.0で全問正解 -> ECE は0のはず。
    probs = torch.tensor([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]])
    labels = torch.tensor([0, 1, 0])
    ece = expected_calibration_error(probs, labels)
    assert ece == pytest.approx(0.0, abs=1e-6)


def test_expected_calibration_error_positive_when_overconfident_and_wrong() -> None:
    probs = torch.tensor([[0.99, 0.01], [0.99, 0.01]])
    labels = torch.tensor([1, 1])  # 確信度は高いが両方はずれ
    ece = expected_calibration_error(probs, labels)
    assert ece > 0.5


def test_slot_facts_to_beliefs_below_existence_threshold_is_empty() -> None:
    facts = SlotFacts(
        entity_gt_id="gt:Pallet_0001",
        existence_prob=0.1,
        zone_idx=0,
        zone_confidence=0.9,
        floor_xy=(0.0, 0.0),
        type_idx=0,
        type_confidence=0.9,
        state_idx=0,
        state_confidence=0.9,
    )
    assert slot_facts_to_beliefs(facts, datetime.now(UTC)) == []


def test_slot_facts_to_beliefs_emits_type_zone_disposition() -> None:
    facts = SlotFacts(
        entity_gt_id="gt:Pallet_0001",
        existence_prob=0.9,
        zone_idx=2,
        zone_confidence=0.8,
        floor_xy=(1.0, 2.0),
        type_idx=0,
        type_confidence=0.7,
        state_idx=0,
        state_confidence=0.6,
    )
    beliefs = slot_facts_to_beliefs(facts, datetime.now(UTC))
    predicates = {b.predicate for b in beliefs}
    assert predicates == {"rdf:type", "gt:currentZone", "gt:disposition"}
    assert all(b.subject == "gt:Pallet_0001" for b in beliefs)
