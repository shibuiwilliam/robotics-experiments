"""S6 リサイクル選別の真値導出（T13）。

真クラス × 規制規則（disposal_route）で正レーンを機械導出し、コスト行列で任意割当の
業務コストを返す。ORコア非依存（common のみ）。
"""

from __future__ import annotations

ESCALATE = "ESCALATE"  # 人間委譲（接地不能・低確信）


def correct_lane(true_class: str, disposal_route: dict[str, str]) -> str:
    if true_class not in disposal_route:
        raise ValueError(f"未知のクラス {true_class!r}")
    return disposal_route[true_class]


def route_cost(
    true_class: str,
    assigned_lane: str,
    cost: dict[str, dict[str, float]],
    escalation_cost: float,
) -> float:
    if assigned_lane == ESCALATE:
        return escalation_cost
    return cost[true_class][assigned_lane]


def is_high_cost_error(
    true_class: str,
    assigned_lane: str,
    cost: dict[str, dict[str, float]],
    high_cost_threshold: float,
) -> bool:
    """高コスト誤り（例: 電池を発火レーン以外へ）= コストが閾値以上。委譲は対象外。"""
    if assigned_lane == ESCALATE:
        return False
    return cost[true_class][assigned_lane] >= high_cost_threshold
