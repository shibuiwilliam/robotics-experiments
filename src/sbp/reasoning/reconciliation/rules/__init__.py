"""五類型の検出規則（設計書 §8.2）。

1 類型 1 ファイル。規則を増やすときも「どの類型の下にあるか」を先に決める——類型は業務側の
解決担当と処置に対応しており、類型に収まらない検出は照合ではなく別のサービスである。
"""

from __future__ import annotations

from sbp.reasoning.reconciliation.rules.kind1 import RecordedPresence, detect_existence
from sbp.reasoning.reconciliation.rules.kind2 import detect_state
from sbp.reasoning.reconciliation.rules.kind3 import TimedClaim, detect_time
from sbp.reasoning.reconciliation.rules.kind4 import detect_identity
from sbp.reasoning.reconciliation.rules.kind5 import NormViolation, detect_norm

__all__ = [
    "NormViolation",
    "RecordedPresence",
    "TimedClaim",
    "detect_existence",
    "detect_identity",
    "detect_norm",
    "detect_state",
    "detect_time",
]
