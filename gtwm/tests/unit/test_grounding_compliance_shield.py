from __future__ import annotations

import pytest
import torch

from gtwm.grounding.probes import Probe
from gtwm.grounding.shield import ComplianceShield
from gtwm.wm.dynamics import Dynamics

pytestmark = pytest.mark.unit

SLOT_DIM = 8
N_ZONES = 4


def _tiny_dynamics() -> Dynamics:
    return Dynamics(
        slot_dim=SLOT_DIM,
        d_model=16,
        n_layers=1,
        n_heads=2,
        cond_dim=4,
        action_dim=2,
        ensemble_size=1,
    )


def test_compliance_shield_mask_shape_and_dtype() -> None:
    torch.manual_seed(0)
    dynamics = _tiny_dynamics()
    probe = Probe(slot_dim=SLOT_DIM, n_zones=N_ZONES)
    slots_t = torch.zeros(1, 3, SLOT_DIM)
    shield = ComplianceShield(
        dynamics=dynamics,
        probe=probe,
        slots_t=slots_t,
        cond=None,
        zone_capacities=torch.full((N_ZONES,), float("inf")),
    )
    candidates = torch.randn(6, 4, 2)
    mask = shield(candidates)
    assert mask.shape == (6,)
    assert mask.dtype == torch.bool


def test_compliance_shield_rejects_zero_capacity_zone() -> None:
    """capacity=0 の禁止ゾーンを設定すると、そのゾーンへの所属確率が高い候補は
    RestrictedZoneShield と同様に棄却されるべき（単一禁止ゾーンの特殊ケース）。"""
    torch.manual_seed(0)
    dynamics = _tiny_dynamics()
    probe = Probe(slot_dim=SLOT_DIM, n_zones=N_ZONES)
    with torch.no_grad():
        probe.zone_head.weight.zero_()
        probe.zone_head.bias.zero_()  # 一様分布 1/N_ZONES = 0.25 を全候補に強制

    slots_t = torch.zeros(1, 2, SLOT_DIM)
    capacities = torch.full((N_ZONES,), float("inf"))
    capacities[1] = 0.0  # ゾーン1を禁止
    shield = ComplianceShield(
        dynamics=dynamics,
        probe=probe,
        slots_t=slots_t,
        cond=None,
        zone_capacities=capacities,
        capacity_violation_threshold=0.5,
    )
    candidates = torch.randn(5, 3, 2)
    single_zone_worst, capacity_worst = shield.symbolize_and_check(candidates)
    assert single_zone_worst.shape == (5,)
    assert capacity_worst.shape == (5,)
    # 期待占有数 = 2スロット * 0.25 = 0.5 > capacity(0) なので sigmoid(excess) > 0.5
    assert bool((capacity_worst > 0.5).all())
    mask = shield(candidates)
    assert not bool(mask.any())


def test_compliance_shield_unlimited_capacity_only_checks_single_zone() -> None:
    """容量無制限（inf）なら capacity_violation は常に~0で、単一ゾーン項のみが効く。"""
    torch.manual_seed(2)
    dynamics = _tiny_dynamics()
    probe = Probe(slot_dim=SLOT_DIM, n_zones=N_ZONES)
    with torch.no_grad():
        probe.zone_head.weight.zero_()
        probe.zone_head.bias.zero_()

    slots_t = torch.zeros(1, 2, SLOT_DIM)
    shield = ComplianceShield(
        dynamics=dynamics,
        probe=probe,
        slots_t=slots_t,
        cond=None,
        zone_capacities=torch.full((N_ZONES,), float("inf")),
    )
    candidates = torch.randn(4, 3, 2)
    _single_zone_worst, capacity_worst = shield.symbolize_and_check(candidates)
    assert torch.allclose(capacity_worst, torch.zeros_like(capacity_worst), atol=1e-4)
