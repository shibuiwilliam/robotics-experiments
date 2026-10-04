"""アクション層。意味契約、前提検証、承認、実行、補償。設計書 §9。

責務: ActionType の意味契約の検証、三面の前提検証、承認、冪等な実行、補償、``ActionEvent`` の発行。
非責務: 業務システムへの書き込みそのもの（``integration.writeback``）、期待の検証
（``reasoning.expectation``）。

**実行フローの順序を省略できない形にしている**（設計書 §9.2）。:class:`ActionRequest` は
段階を進めるメソッドしか持たず、承認を飛ばして実行する経路は無い。
"""

from __future__ import annotations

from sbp.actions.contracts import (
    ActionContract,
    ActionRegistry,
    ApprovalPolicy,
    ApprovalTrigger,
    Effect,
    Precondition,
    RateLimit,
    RiskClass,
    parse_duration,
)
from sbp.actions.execute import (
    ActionEvent,
    ActionRequest,
    ActionStage,
    Approval,
    Override,
    RateLimitState,
    require_satisfied,
)
from sbp.actions.expectations import expectation_for, expected_effect_for
from sbp.actions.preconditions import (
    ConditionResult,
    PreconditionReport,
    check_preconditions,
)
from sbp.actions.requests import ExecutionCommand

__all__ = [
    "ActionContract",
    "ActionEvent",
    "ActionRegistry",
    "ActionRequest",
    "ActionStage",
    "Approval",
    "ApprovalPolicy",
    "ApprovalTrigger",
    "ConditionResult",
    "Effect",
    "ExecutionCommand",
    "Override",
    "Precondition",
    "PreconditionReport",
    "RateLimit",
    "RateLimitState",
    "RiskClass",
    "check_preconditions",
    "expectation_for",
    "expected_effect_for",
    "parse_duration",
    "require_satisfied",
]
