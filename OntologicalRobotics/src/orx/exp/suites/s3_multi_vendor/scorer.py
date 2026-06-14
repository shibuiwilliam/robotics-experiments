"""S3 採点: 段取り替え＋故障を含むエピソード列を1条件分シミュレートし指標を出す（決定的）。

割当正答率・スループット（期待完遂）・再計画回数（縮退運転）と、OR-full の台帳較正
（Brier 信頼性項が故障に追従するか, T6）を返す。乱数は seed 由来で再現的。
"""

from __future__ import annotations

import numpy as np

from orx.common.schemas import StrictModel
from orx.exp.suites.s3_multi_vendor.ledger import CapabilityLedger
from orx.exp.suites.s3_multi_vendor.model import S3World
from orx.exp.suites.s3_multi_vendor.reference import RoundRobinState, allocate
from orx.oracle.scenarios.s3 import (
    allocation_correct,
    chosen_true_success,
    true_success,
)


class EpisodeMetric(StrictModel):
    episode: int
    phase: str  # initial | setup | fault
    accuracy: float
    throughput: float
    replans: int
    brier_reliability: float  # 台帳推定 vs 真率（OR-full のみ意味を持つ）
    fault_machine_estimate: float  # 故障機体の台帳推定（追従の可視化）


class ConditionRun(StrictModel):
    condition: str
    per_episode: list[EpisodeMetric]


def _phase(world: S3World, e: int) -> str:
    if e >= world.fault_step:
        return "fault"
    if e >= world.setup_change_step:
        return "setup"
    return "initial"


def simulate_condition(world: S3World, condition: str, rng: np.random.Generator) -> ConditionRun:
    ledger = CapabilityLedger()
    for m in world.machines:
        ledger.declare(m)
    rr = RoundRobinState()
    learns = condition != "round-robin"
    factor = world.fault_degradation
    faulted = frozenset({world.fault_machine})
    per_episode: list[EpisodeMetric] = []

    for e in range(world.n_episodes):
        phase = _phase(world, e)
        product = world.products[
            world.setup_change_product if e >= world.setup_change_step else world.initial_product
        ]
        cur_faulted = faulted if e >= world.fault_step else frozenset()
        correct = 0
        thr = 0.0
        replans = 0
        for _ in range(world.tasks_per_episode):
            chosen = allocate(condition, ledger, world.machines, product, world, rr)
            if allocation_correct(
                world.machines, product, chosen, cur_faulted, factor, world.tolerance
            ):
                correct += 1
            thr += chosen_true_success(world.machines, product, chosen, cur_faulted, factor)
            if learns and chosen is not None:
                ts = chosen_true_success(world.machines, product, chosen, cur_faulted, factor)
                success = rng.random() < ts
                ledger.record(chosen, product.material, success)
                if not success:
                    others = [m for m in world.machines if m.machine_id != chosen]
                    retry = allocate(condition, ledger, others, product, world, rr)
                    if retry is not None:
                        replans += 1
                        rts = chosen_true_success(
                            world.machines, product, retry, cur_faulted, factor
                        )
                        ledger.record(retry, product.material, rng.random() < rts)
        per_episode.append(
            EpisodeMetric(
                episode=e,
                phase=phase,
                accuracy=round(correct / world.tasks_per_episode, 6),
                throughput=round(thr / world.tasks_per_episode, 6),
                replans=replans,
                brier_reliability=_brier_reliability(world, ledger, e),
                fault_machine_estimate=_fault_estimate(world, ledger),
            )
        )
    return ConditionRun(condition=condition, per_episode=per_episode)


def _material_product(world: S3World, material: str):
    for name in (world.setup_change_product, world.initial_product):
        if world.products[name].material == material:
            return world.products[name]
    return None


def _brier_reliability(world: S3World, ledger: CapabilityLedger, e: int) -> float:
    """訪問済み (機体,素材) セルの (推定 - 真率)^2 平均（較正の本体, T6）。"""
    factor = world.fault_degradation
    cur_faulted = frozenset({world.fault_machine}) if e >= world.fault_step else frozenset()
    by_id = {m.machine_id: m for m in world.machines}
    terms: list[float] = []
    for (machine_id, material), (n, _) in ledger._stats.items():
        if n == 0:
            continue
        product = _material_product(world, material)
        machine = by_id.get(machine_id)
        if product is None or machine is None:
            continue
        deg = factor if (machine.degrades and machine_id in cur_faulted) else 1.0
        true_rate = true_success(machine, product, deg)
        diff = ledger.estimate(machine_id, material) - true_rate
        terms.append(diff * diff)
    return round(sum(terms) / len(terms), 6) if terms else 0.0


def _fault_estimate(world: S3World, ledger: CapabilityLedger) -> float:
    product = world.products[world.setup_change_product]
    return round(ledger.estimate(world.fault_machine, product.material), 6)
