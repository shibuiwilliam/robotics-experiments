"""S5 病院内搬送の真値導出（T12）。

規範（禁止）と区画分類から、ある経路の規範違反数を機械導出する。custody監査の真値は
「真の通行ステップ数（経路長）」であり、記録ステップ数との比で完全性を測る。
ORコア非依存（プリミティブのみ）。
"""

from __future__ import annotations


def forbidden_zone_classes(item_class: str, norms: list) -> set[str]:
    """その物分類が通過してはならない区画分類の集合。"""
    out: set[str] = set()
    for n in norms:
        if n.item_class == item_class and n.modality == "prohibition":
            out.add(n.forbidden_zone_class)
    return out


def count_violations(
    route: list[str], item_class: str, norms: list, zone_class: dict[str, str]
) -> int:
    """経路が禁止区画分類を通過した回数（規範違反数）。"""
    forbidden = forbidden_zone_classes(item_class, norms)
    return sum(1 for z in route if zone_class.get(z) in forbidden)


def audit_completeness(recorded_steps: int, true_steps: int) -> float:
    """custody連鎖の完全性 = 記録通行ステップ / 真の通行ステップ（監査可能性）。"""
    if true_steps <= 0:
        return 1.0
    return min(1.0, recorded_steps / true_steps)
