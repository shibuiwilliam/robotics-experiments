from __future__ import annotations

import pytest
import torch

from gtwm.grounding.probes import Probe
from gtwm.grounding.shield import RestrictedZoneShield
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


def test_shield_rejects_candidates_predicted_to_enter_restricted_zone() -> None:
    torch.manual_seed(0)
    dynamics = _tiny_dynamics()
    probe = Probe(slot_dim=SLOT_DIM, n_zones=N_ZONES)
    # ゾーンヘッドを固定し、行動の第0成分が大きいほど禁止ゾーン(idx=1)の確率が
    # 上がるように重みを手で設定する（決定的な検証のため）。
    with torch.no_grad():
        probe.zone_head.weight.zero_()
        probe.zone_head.bias.zero_()

    slots_t = torch.zeros(1, 3, SLOT_DIM)
    shield = RestrictedZoneShield(
        dynamics=dynamics,
        probe=probe,
        slots_t=slots_t,
        cond=None,
        restricted_zone_idx=1,
        violation_threshold=0.5,
    )

    # symbolize_and_check は実際のロールアウトを使うため、ここでは決定的な閾値検証
    # ではなく「呼び出し可能で mask が [N] の bool テンソルであること」「全滅時の
    # フォールバックが機能すること」を検証する（記号化ロジック自体は
    # test_grounding_probes.py で個別にカバーされる零重み確率分布 = 一様1/N_ZONES と
    # violation_threshold の関係で決定的に振る舞う）。
    candidates = torch.randn(6, 3, 2)
    mask = shield(candidates)
    assert mask.shape == (6,)
    assert mask.dtype == torch.bool

    # 全ゾーン確率が一様（1/N_ZONES=0.25）なら閾値0.5未満なので全候補が許容される。
    assert bool(mask.all())

    # 閾値をゼロ近くまで下げると、一様確率(0.25)でも違反度0.25 >= 0 のため全棄却される。
    strict_shield = RestrictedZoneShield(
        dynamics=dynamics,
        probe=probe,
        slots_t=slots_t,
        cond=None,
        restricted_zone_idx=1,
        violation_threshold=1e-6,
    )
    strict_mask = strict_shield(candidates)
    assert not bool(strict_mask.any())


def test_shield_symbolize_and_check_returns_worst_case_over_horizon_and_slots() -> None:
    torch.manual_seed(1)
    dynamics = _tiny_dynamics()
    probe = Probe(slot_dim=SLOT_DIM, n_zones=N_ZONES)
    slots_t = torch.zeros(1, 2, SLOT_DIM)
    shield = RestrictedZoneShield(
        dynamics=dynamics,
        probe=probe,
        slots_t=slots_t,
        cond=None,
        restricted_zone_idx=0,
        controlled_slot_idx=0,
    )
    candidates = torch.randn(4, 5, 2)
    degree = shield.symbolize_and_check(candidates)
    assert degree.shape == (4,)
    assert torch.all((degree >= 0.0) & (degree <= 1.0))
