"""S3 多ベンダー製造ラインの真値導出（T10）。

真の能力プロファイル（可搬上限・素材別成功率）と故障劣化から、(機体, 品種) の真の成功率と
最良実現可能割当を機械導出する。ORコア非依存（exp/suites を import しない・構造的型のみ）。
"""

from __future__ import annotations

from typing import Protocol


class _Machine(Protocol):
    machine_id: str
    true_payload_kg: float
    true_material_success: dict[str, float]
    degrades: bool


class _Product(Protocol):
    weight_kg: float
    material: str


def true_success(machine: _Machine, product: _Product, degradation: float = 1.0) -> float:
    """(機体, 品種) の真の成功率。可搬上限超過は 0、それ以外は素材別成功率×劣化係数。"""
    if product.weight_kg > machine.true_payload_kg:
        return 0.0
    base = machine.true_material_success.get(
        product.material, machine.true_material_success.get("default", 0.0)
    )
    return base * degradation


def _degradation(machine: _Machine, faulted: frozenset[str], factor: float) -> float:
    return factor if (machine.degrades and machine.machine_id in faulted) else 1.0


def best_feasible_success(
    machines: list[_Machine], product: _Product, faulted: frozenset[str], factor: float
) -> float:
    """全機体のうち真の成功率が最大の値（採点の基準）。"""
    return max(
        (true_success(m, product, _degradation(m, faulted, factor)) for m in machines),
        default=0.0,
    )


def allocation_correct(
    machines: list[_Machine],
    product: _Product,
    chosen_id: str | None,
    faulted: frozenset[str],
    factor: float,
    tolerance: float,
) -> bool:
    """選んだ機体の真の成功率が、全機体の最良に許容内で達しているか。"""
    if chosen_id is None:
        return False
    by_id = {m.machine_id: m for m in machines}
    chosen = by_id.get(chosen_id)
    if chosen is None:
        return False
    got = true_success(chosen, product, _degradation(chosen, faulted, factor))
    best = best_feasible_success(machines, product, faulted, factor)
    return got >= best - tolerance


def chosen_true_success(
    machines: list[_Machine],
    product: _Product,
    chosen_id: str | None,
    faulted: frozenset[str],
    factor: float,
) -> float:
    """選んだ機体の真の成功率（スループット採点用）。未割当は 0。"""
    if chosen_id is None:
        return 0.0
    by_id = {m.machine_id: m for m in machines}
    chosen = by_id.get(chosen_id)
    if chosen is None:
        return 0.0
    return true_success(chosen, product, _degradation(chosen, faulted, factor))
