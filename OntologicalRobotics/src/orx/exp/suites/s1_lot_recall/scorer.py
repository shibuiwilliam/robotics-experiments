"""S1 採点: 回収列挙の精度・現在地・隔離完遂・誤隔離。"""

from __future__ import annotations

from orx.common.schemas import StrictModel
from orx.exp.suites.s1_lot_recall.reference import RecallAnswer
from orx.oracle.scenarios.s1 import RecallTruth


class RecallScore(StrictModel):
    membership_precision: float
    membership_recall: float
    membership_f1: float
    location_accuracy: float  # 真の対象のうち現在地まで正しく特定した割合
    completion: float  # 隔離完遂率 = 正しく特定かつ正しい現在地（取りに行ける）
    false_quarantine: int  # 誤隔離数（非対象を対象と誤判定）
    success: bool  # 完遂率100% かつ 誤隔離0


def score_recall(answer: RecallAnswer, truth: RecallTruth) -> RecallScore:
    true_targets = set(truth.targets)
    reported = set(answer.targets)
    tp = reported & true_targets
    n_true = len(true_targets)
    n_rep = len(reported)

    precision = len(tp) / n_rep if n_rep else (1.0 if n_true == 0 else 0.0)
    recall = len(tp) / n_true if n_true else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    located = {bc for bc in tp if answer.targets[bc] == truth.targets[bc]}
    location_accuracy = len(located) / n_true if n_true else 1.0
    completion = location_accuracy
    false_quarantine = len(reported - true_targets)
    return RecallScore(
        membership_precision=round(precision, 6),
        membership_recall=round(recall, 6),
        membership_f1=round(f1, 6),
        location_accuracy=round(location_accuracy, 6),
        completion=round(completion, 6),
        false_quarantine=false_quarantine,
        success=(completion >= 1.0 - 1e-9 and false_quarantine == 0),
    )
