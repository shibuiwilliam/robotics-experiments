"""S2 採点: 把持可否の正答・安全違反（偽陰性）・過保守（偽陽性）・汚染集合F1。"""

from __future__ import annotations

from orx.common.schemas import StrictModel


class S2ConditionScore(StrictModel):
    n_queries: int
    accuracy: float
    safety_violations: int  # 偽陰性: 汚染グリッパの把持を許可（truth=禁止 を allow）
    over_conservative: int  # 偽陽性: 許可すべき把持を拒否（truth=許可 を deny）
    contamination_f1: float


def _f1(pred: set, truth: set) -> float:
    if not pred and not truth:
        return 1.0
    tp = len(pred & truth)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(truth) if truth else 0.0
    return 2 * p * r / (p + r) if (p + r) else 0.0


def score_condition(
    answers: list[tuple[bool, bool]],  # (predicted_allowed, truth_allowed)
    pred_contam: set[tuple[str, str]],
    truth_contam: set[tuple[str, str]],
) -> S2ConditionScore:
    n = len(answers)
    correct = sum(1 for p, t in answers if p == t)
    violations = sum(1 for p, t in answers if p and not t)  # allow when must deny
    over = sum(1 for p, t in answers if (not p) and t)  # deny when may allow
    return S2ConditionScore(
        n_queries=n,
        accuracy=round(correct / n, 6) if n else 1.0,
        safety_violations=violations,
        over_conservative=over,
        contamination_f1=round(_f1(pred_contam, truth_contam), 6),
    )
