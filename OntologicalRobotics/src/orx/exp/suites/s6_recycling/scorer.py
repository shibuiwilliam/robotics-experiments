"""S6 採点（X6 非対称コスト）: コスト加重・高コスト誤り・委譲率・較正Brier。"""

from __future__ import annotations

from orx.common.schemas import StrictModel
from orx.exp.suites.s6_recycling.model import S6Object, S6World
from orx.oracle.scenarios.s6 import ESCALATE, is_high_cost_error, route_cost


class S6ConditionScore(StrictModel):
    mean_cost: float  # コスト加重スコア（低いほど良い）
    high_cost_errors: int  # 高コスト誤り（電池の誤レーン等）
    escalation_rate: float  # 人間委譲率
    throughput: float  # 自動処理率 = 1 - 委譲率
    routed_accuracy: float  # 委譲を除く割当の正答率
    brier: float  # 接地確信度の較正（接地条件のみ。委譲のみは 0.0）


def score(
    assignments: list[tuple[S6Object, str, float]],  # (obj, assigned_lane, confidence)
    world: S6World,
    high_cost_threshold: float,
) -> S6ConditionScore:
    n = len(assignments)
    total_cost = 0.0
    high = 0
    escalations = 0
    routed = 0
    routed_correct = 0
    brier_terms: list[float] = []
    for obj, lane, conf in assignments:
        total_cost += route_cost(obj.true_class, lane, world.cost, world.escalation_cost)
        if is_high_cost_error(obj.true_class, lane, world.cost, high_cost_threshold):
            high += 1
        if lane == ESCALATE:
            escalations += 1
        else:
            routed += 1
            if lane == world.disposal_route[obj.true_class]:
                routed_correct += 1
            # 接地確信度の較正: conf を「正クラス接地の確率」とみなす
            grounded_correct = 1.0 if lane == world.disposal_route[obj.true_class] else 0.0
            brier_terms.append((conf - grounded_correct) ** 2)
    return S6ConditionScore(
        mean_cost=round(total_cost / n, 6) if n else 0.0,
        high_cost_errors=high,
        escalation_rate=round(escalations / n, 6) if n else 0.0,
        throughput=round(routed / n, 6) if n else 0.0,
        routed_accuracy=round(routed_correct / routed, 6) if routed else 0.0,
        brier=round(sum(brier_terms) / len(brier_terms), 6) if brier_terms else 0.0,
    )
