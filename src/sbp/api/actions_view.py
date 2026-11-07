"""アクション契約の要約（UI の承認フォームが読む。M8-22、ADR-0021）。

契約（``actions/*.yaml``）が唯一の正で、UI に写しを持たせない（契約を変えるたびに UI が古くなり、
承認の要否を取り違える）。ここは契約を**人が読む形に要約する純粋な関数**で、GraphQL の型への変換は
:mod:`sbp.api.schema`。

**承認の要否は「条件」として返す。** 要否は実行時の深刻度で決まりうる（``low`` で
``severity_at_least_high``）。UI が深刻度を知っていても、判定は契約の ``requires_approval`` が
行う——ここで再実装しない。返すのは ``always`` / ``if_severity_high`` / ``never`` のどれか。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sbp.actions.contracts import ApprovalTrigger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.actions.contracts import ActionContract

__all__ = ["ActionSummary", "ApprovalMode", "summarize"]

ApprovalMode = Literal["always", "if_severity_high", "never"]


@dataclass(frozen=True, slots=True)
class ActionSummary:
    """契約 1 件の要約。

    Attributes:
        action_type: アクション型 IRI。
        targets: 対象の型 IRI。
        risk_class: リスク級（``low`` / ``medium`` / ``high``）。
        approval: 承認の要否（条件）。
        approval_role: 承認者のロール IRI。
        preconditions: 面（``symbolic`` / ``physical`` / ``normative``）→ 前提の説明。
        effects: 面（``business`` / ``physical``）→ 期待する効果の ``(説明, 期限)``。
        compensation: 補償アクション型 IRI（**表示するだけ**。実行の入口は無い。H-08）。
        rate_limit: ``(窓, 最大回数)``。無ければ ``None``。
    """

    action_type: str
    targets: str
    risk_class: str
    approval: ApprovalMode
    approval_role: str
    preconditions: dict[str, tuple[str, ...]]
    effects: dict[str, tuple[tuple[str, str], ...]]
    compensation: str
    rate_limit: tuple[str, int] | None


def _approval_mode(contract: ActionContract) -> ApprovalMode:
    """承認の要否（``medium`` 以上は深刻度に依らず必須。設計書 §9.3）。"""
    if contract.risk_class.requires_approval or contract.approvals.required_if is (
        ApprovalTrigger.ALWAYS
    ):
        return "always"
    if contract.approvals.required_if is ApprovalTrigger.SEVERITY_AT_LEAST_HIGH:
        return "if_severity_high"
    return "never"


def summarize(contracts: Sequence[ActionContract]) -> tuple[ActionSummary, ...]:
    """契約を要約する（アクション型の昇順）。

    Args:
        contracts: アクション契約。
    """
    return tuple(
        ActionSummary(
            action_type=contract.action_type,
            targets=contract.targets,
            risk_class=contract.risk_class.value,
            approval=_approval_mode(contract),
            approval_role=contract.approvals.role,
            preconditions={
                face: tuple(condition.description for condition in conditions)
                for face, conditions in contract.preconditions.items()
            },
            effects={
                face: tuple((effect.description, effect.within) for effect in effects)
                for face, effects in contract.effects.items()
            },
            compensation=contract.compensation,
            rate_limit=(
                None
                if contract.rate_limit is None
                else (contract.rate_limit.per_target_window, contract.rate_limit.per_target_max)
            ),
        )
        for contract in sorted(contracts, key=lambda item: item.action_type)
    )
