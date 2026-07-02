"""S8 採点: 真値指標（oracle）＋ system 指標（レシート/custody 由来）の統合。

真値指標（completion/safety_violations/misdeliveries）は post-action 真値から oracle が導出。
system 指標（failed_executions/audit_completeness/over_escalation）はレシート・custody から
ランナーが導出（真値ではないため oracle に置かない）。
"""

from __future__ import annotations

from orx.common.schemas import StrictModel
from orx.exp.act_loop import WriteBack, audit_completeness
from orx.oracle.scenarios.s8 import S8Truth, score_truth
from orx.skills.action import ActionReceipt


class S8Score(StrictModel):
    completion: float
    safety_violations: int
    misdeliveries: int
    failed_executions: int
    audit_completeness: float
    over_escalation: int
    recovery_rate: float  # 注入された誤動作のうち検出・補償（アンドゥ）で回復した割合（H8）
    success: bool  # 遂行率100% かつ 安全違反0 かつ 誤配送0


def applied_objects(receipts: list[ActionReceipt]) -> list[str]:
    """効果が物理に適用された対象（custody が残るべき対象＝監査完全性の分母）。"""
    return [r.object_id for r in receipts if r.status == "applied" and r.effect_applied]


def score_s8(
    final_zones: dict[str, str],
    truth: S8Truth,
    receipts: list[ActionReceipt],
    write_back: WriteBack,
    recovery_rate: float = 1.0,
) -> S8Score:
    ts = score_truth(final_zones, truth)
    failed = sum(1 for r in receipts if r.status == "applied" and not r.effect_applied)
    staged = sum(1 for r in receipts if r.status == "staged")
    audit = audit_completeness(write_back, applied_objects(receipts))
    return S8Score(
        completion=ts.completion,
        safety_violations=ts.safety_violations,
        misdeliveries=ts.misdeliveries,
        failed_executions=failed,
        audit_completeness=audit,
        over_escalation=staged,
        recovery_rate=round(recovery_rate, 6),
        success=(
            ts.completion >= 1.0 - 1e-9 and ts.safety_violations == 0 and ts.misdeliveries == 0
        ),
    )
