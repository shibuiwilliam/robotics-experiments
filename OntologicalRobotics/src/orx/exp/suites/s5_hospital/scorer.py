"""S5 採点: 規範違反数・custody監査完全性・規範コスト（効率劣化）（決定的）。"""

import numpy as np

from orx.common.schemas import StrictModel
from orx.exp.suites.s5_hospital.model import S5World
from orx.exp.suites.s5_hospital.reference import plan_route, record_custody, shortest_route
from orx.oracle.scenarios.s5 import audit_completeness, count_violations


class S5ConditionScore(StrictModel):
    violations: int  # 規範違反（禁止区画の通過）総数
    audit_completeness: float  # 監査クエリ完全回答率（custody連鎖の再構成）
    normative_cost: float  # 規範遵守による追加ホップ平均（効率劣化）
    delivered: int  # 搬送完遂数


def score_condition(
    world: S5World,
    condition: str,
    custody_gap_rate: float = 0.0,
    rng: np.random.Generator | None = None,
) -> S5ConditionScore:
    """規範遵守・監査完全性・効率を採点する。

    custody_gap_rate>0（R-3c 頑健性ノブ）: 記録済み custody エントリを確率的に欠落させ、
    監査完全性の劣化曲線を描く。**gap=0 では決定的で従来挙動を保存**（反証ゲート不変）。
    rng は seed 依存にして掃引点を seed 毎に変動させる（R-3b: seeds が掃引解像度に効く）。
    """
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
            n_recorded = len(recorded)
            if custody_gap_rate > 0.0 and rng is not None and n_recorded:
                # 各記録を gap_rate で欠落（監査連鎖の再構成が不完全になる）
                kept = sum(1 for _ in range(n_recorded) if rng.random() >= custody_gap_rate)
                n_recorded = int(kept)
            completeness.append(audit_completeness(n_recorded, len(route)))
    n = len(world.transports)
    return S5ConditionScore(
        violations=violations,
        audit_completeness=round(sum(completeness) / len(completeness), 6) if completeness else 1.0,
        normative_cost=round(sum(extra_hops) / n, 6) if n else 0.0,
        delivered=delivered,
    )
