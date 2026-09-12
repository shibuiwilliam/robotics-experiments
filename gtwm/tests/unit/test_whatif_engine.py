"""WHAT-IF エンジンの純粋なロジック部分のユニットテスト。

`run_whatif()` 自体は `train_probes()`（DINOv2 エンコーダの読込・学習）を経由するため
1件1秒以内・ネットワーク不要という unit テストの制約に合わない。エンドツーエンドの
動作確認は `gtwm whatif "..."` の手動実行（docs/status.md に実測値を記録）で行う。
ここでは KPI 集計ロジック（`_queue_len_for_realization`）を、実際の Probe だが極小の
合成スロットに対して検証する（ネットワーク・学習不要）。
"""

from __future__ import annotations

import pytest
import torch

from gtwm.grounding.probes import N_ZONES, Probe
from gtwm.kg.whatif.engine import _effective_samples, _queue_len_for_realization
from gtwm.sim.env import ZONE_NAMES

pytestmark = pytest.mark.unit


def _probe_forcing_zone(slot_dim: int, forced_zone_idx: int) -> Probe:
    """zone_head が常に `forced_zone_idx` を最大ロジットにするよう重みを固定した Probe。"""
    probe = Probe(slot_dim=slot_dim)
    with torch.no_grad():
        probe.zone_head.weight.zero_()
        probe.zone_head.bias.zero_()
        probe.zone_head.bias[forced_zone_idx] = 10.0
        probe.existence_head.weight.zero_()
        probe.existence_head.bias.fill_(10.0)  # 常に existence_prob ~= 1
    return probe


def test_queue_len_counts_existing_slots_without_filter() -> None:
    slot_dim = 4
    probe = _probe_forcing_zone(slot_dim, forced_zone_idx=0)
    future_slots = torch.zeros(5, slot_dim)  # 5スロット、全て実在扱い
    count = _queue_len_for_realization(probe, future_slots, zone_filter_idx=None)
    assert count == 5.0


def test_queue_len_filters_by_zone() -> None:
    slot_dim = 4
    zone_idx = ZONE_NAMES.index("Pick")
    probe = _probe_forcing_zone(slot_dim, forced_zone_idx=zone_idx)
    future_slots = torch.zeros(3, slot_dim)

    matching = _queue_len_for_realization(probe, future_slots, zone_filter_idx=zone_idx)
    assert matching == 3.0

    other_idx = (zone_idx + 1) % N_ZONES
    non_matching = _queue_len_for_realization(probe, future_slots, zone_filter_idx=other_idx)
    assert non_matching == 0.0


def test_queue_len_respects_existence_threshold() -> None:
    slot_dim = 4
    probe = Probe(slot_dim=slot_dim)
    with torch.no_grad():
        probe.existence_head.weight.zero_()
        probe.existence_head.bias.fill_(-10.0)  # 常に existence_prob ~= 0
    future_slots = torch.zeros(4, slot_dim)
    count = _queue_len_for_realization(probe, future_slots, zone_filter_idx=None)
    assert count == 0.0


def test_effective_samples_is_bounded_and_at_least_one() -> None:
    assert _effective_samples(0) == 1
    assert _effective_samples(5) == 5
    assert _effective_samples(10_000) < 10_000
