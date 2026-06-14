"""S7 採点: 入居者ごとの配送決定から 成功率・誤配送率・確認(委譲)率を集計（決定的）。"""

from __future__ import annotations

import numpy as np

from orx.common.schemas import StrictModel
from orx.exp.suites.s7_ownership.model import S7Episode, S7World
from orx.exp.suites.s7_ownership.reference import decide
from orx.oracle.scenarios.s7 import delivery_outcome


class S7ConditionScore(StrictModel):
    success_rate: float  # 正しい入居者へ自動配送
    misdelivery_rate: float  # 別人へ誤配送（業務上の重大失敗）
    escalation_rate: float  # 確認行動（X5: 人間へ確認）


def score_condition(
    world: S7World, episode: S7Episode, condition: str, rng: np.random.Generator
) -> S7ConditionScore:
    n = len(world.residents)
    success = misdeliv = escal = 0
    for r in world.residents:
        decision = decide(condition, r, episode, world, rng)
        outcome = delivery_outcome(decision, r, episode.objects)
        if outcome == "success":
            success += 1
        elif outcome == "misdelivery":
            misdeliv += 1
        else:
            escal += 1
    return S7ConditionScore(
        success_rate=round(success / n, 6),
        misdelivery_rate=round(misdeliv / n, 6),
        escalation_rate=round(escal / n, 6),
    )
