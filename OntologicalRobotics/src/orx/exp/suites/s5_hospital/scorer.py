"""S5 採点: 規範違反数・custody監査完全性・規範コスト（効率劣化）（決定的）。"""

from __future__ import annotations

from orx.common.schemas import StrictModel
from orx.exp.suites.s5_hospital.model import S5World
from orx.exp.suites.s5_hospital.reference import plan_route, record_custody, shortest_route
from orx.oracle.scenarios.s5 import audit_completeness, count_violations


class S5ConditionScore(StrictModel):
    violations: int  # 規範違反（禁止区画の通過）総数
    audit_completeness: float  # 監査クエリ完全回答率（custody連鎖の再構成）
    normative_cost: float  # 規範遵守による追加ホップ平均（効率劣化）
    delivered: int  # 搬送完遂数


def score_condition(world: S5World, condition: str) -> S5ConditionScore:
    violations = 0
    delivered = 0
    extra_hops: list[int] = []
    completeness: list[float] = []
    for t in world.transports:
        route = plan_route(condition, t, world)
        delivered += 1 if len(route) >= 1 and route[-1] == t.dst else 0
        violations += count_violations(route, t.item_class, world.norms, world.zone_class)
        short = shortest_route(t, world)
        extra_hops.append(max(0, (len(route) - 1) - (len(short) - 1)))
        if t.item_class == world.audited_item_class:
            recorded = record_custody(condition, route)
            completeness.append(audit_completeness(len(recorded), len(route)))
    n = len(world.transports)
    return S5ConditionScore(
        violations=violations,
        audit_completeness=round(sum(completeness) / len(completeness), 6) if completeness else 1.0,
        normative_cost=round(sum(extra_hops) / n, 6) if n else 0.0,
        delivered=delivered,
    )
