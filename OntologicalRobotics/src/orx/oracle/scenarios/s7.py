"""S7 介護施設「所有」の真値導出（T14）。

真の所有者から配送の正否（成功 / 誤配送 / 確認委譲）を機械導出する。
ORコア非依存（exp/suites を import しない・構造的型のみ）。
"""

from __future__ import annotations

from typing import Protocol

ESCALATE = -1  # 確認行動（X5: 人間へ確認）


class _Object(Protocol):
    true_owner: str


def delivery_outcome(decision: int, request_owner: str, objects: list[_Object]) -> str:
    """配送決定の業務帰結。返り値: success | misdelivery | escalation。"""
    if decision == ESCALATE:
        return "escalation"
    if decision < 0 or decision >= len(objects):
        return "misdelivery"  # 不正な対象＝配送失敗扱い
    return "success" if objects[decision].true_owner == request_owner else "misdelivery"


def correct_object_index(request_owner: str, objects: list[_Object]) -> int:
    """その入居者の真の所有物のインデックス（採点・参照用）。無ければ ESCALATE。"""
    for i, o in enumerate(objects):
        if o.true_owner == request_owner:
            return i
    return ESCALATE
