"""承認・実行・補償（設計書 §9.2、§9.3、M5-03）。

実行フローは設計書 §9.2 の順序を崩さない。

1. 前提検証（三面） → 2. 承認 → 3. 実行（冪等） → 4. ``ActionEvent`` → 5. ``Expectation`` 生成

**順序を省略できない形にしている。** :class:`ActionRequest` は段階を進めるメソッドしか持たず、
承認を飛ばして実行することはできない。「開発中だけ飛ばす」経路を作ると、それが本番に残る。

**人の上書きは消さずに残す**（設計書 §9.3）。物理的実行可能性が閾値未満でも人は上書きできるが、
上書きしたこと・理由・誰が、を来歴に残す。上書きを禁じると現場が基盤を迂回し、基盤の外で
アクションが実行されるようになる——それは「信念を持たない実行」であり最悪の結果になる。

**レート制限は対象ごと**（設計書 §9.3）。同じポンプに点検を 10 分おきに起票しない。全体の
スロットリングではなく対象ごとにするのは、「別の設備には出せる」ことを保つためである。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sbp.core.errors import ActionError, ApprovalRequiredError, PreconditionNotMetError
from sbp.core.ids import new_event_id
from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from sbp.actions.contracts import ActionContract
    from sbp.actions.preconditions import PreconditionReport

__all__ = [
    "ActionEvent",
    "ActionRequest",
    "ActionStage",
    "Approval",
    "Override",
    "RateLimitState",
    "planned_expectation_deadlines",
    "require_satisfied",
]

log = get_logger(__name__)


class ActionStage(StrEnum):
    """実行フローの段階（設計書 §9.2）。"""

    REQUESTED = "requested"
    """要求された。前提は未検証。"""
    CHECKED = "checked"
    """前提を検証した（満たしているか、上書きが付いているか）。"""
    APPROVED = "approved"
    """承認された（または承認不要と判定された）。"""
    EXECUTED = "executed"
    """実行した。``ActionEvent`` を発行済み。"""
    COMPENSATED = "compensated"
    """補償した（実行を取り消した）。"""
    REJECTED = "rejected"
    """前提未達または承認却下で実行しなかった。"""


_ALLOWED: dict[ActionStage, frozenset[ActionStage]] = {
    ActionStage.REQUESTED: frozenset({ActionStage.CHECKED, ActionStage.REJECTED}),
    ActionStage.CHECKED: frozenset({ActionStage.APPROVED, ActionStage.REJECTED}),
    ActionStage.APPROVED: frozenset({ActionStage.EXECUTED, ActionStage.REJECTED}),
    ActionStage.EXECUTED: frozenset({ActionStage.COMPENSATED}),
    ActionStage.COMPENSATED: frozenset(),
    ActionStage.REJECTED: frozenset(),
}
"""許可された段階の遷移。承認を飛ばして実行する経路は無い。"""


@dataclass(frozen=True, slots=True)
class Approval:
    """承認 1 件。

    Attributes:
        role: 承認したロール IRI。
        approved_by: 承認した主体の IRI（人・エージェント）。
        approved_at: 承認時刻。
        note: 承認時のコメント。
    """

    role: str
    approved_by: str
    approved_at: datetime
    note: str = ""


@dataclass(frozen=True, slots=True)
class Override:
    """前提未達の上書き（設計書 §9.3）。

    Attributes:
        overridden_by: 上書きした主体の IRI。
        overridden_at: 上書き時刻。
        reason: 理由。**空は許さない**——理由の無い上書きは来歴として機能しない。
        failed_faces: 上書きで通した面。
    """

    overridden_by: str
    overridden_at: datetime
    reason: str
    failed_faces: tuple[str, ...]

    def __post_init__(self) -> None:
        """理由が書かれているか検証する。"""
        if not self.reason:
            raise ActionError(
                "an override must state its reason; overriding a safety guard without a recorded "
                "reason is indistinguishable from a bug (design §9.3)"
            )


@dataclass(frozen=True, slots=True)
class ActionEvent:
    """実行の記録（設計書 §9.2 の 3）。

    Attributes:
        event_id: UUIDv7（不変条件 4）。
        action_type: アクション型 IRI。
        target: 対象実体の IRI。
        command_id: 冪等の鍵。再送しても二重実行されない。
        executed_at: 実行時刻。
        executed_by: 実行した主体の IRI。
        approval: 承認（不要だった場合は ``None``）。
        override: 前提未達の上書き（無ければ ``None``）。
        precondition_note: 前提検証の説明（来歴に残す）。
        correlation_id: 起因となった不一致などの相関 ID。
    """

    event_id: UUID
    action_type: str
    target: str
    command_id: UUID
    executed_at: datetime
    executed_by: str
    approval: Approval | None
    override: Override | None
    precondition_note: str
    correlation_id: UUID | None = None

    @property
    def was_overridden(self) -> bool:
        """安全ガードを人が上書きして実行したか。"""
        return self.override is not None


@dataclass(slots=True)
class RateLimitState:
    """対象ごとの実行履歴（レート制限の判定に使う）。

    Attributes:
        executions: ``(アクション型, 対象)`` → 実行時刻の列。
    """

    executions: dict[tuple[str, str], list[datetime]] = field(default_factory=dict)

    def record(self, *, action_type: str, target: str, at: datetime) -> None:
        """実行を記録する。

        Args:
            action_type: アクション型 IRI。
            target: 対象実体の IRI。
            at: 実行時刻。
        """
        self.executions.setdefault((action_type, target), []).append(ensure_utc(at))

    def count_within(
        self, *, action_type: str, target: str, window: timedelta, at: datetime
    ) -> int:
        """時間窓内の実行回数。

        Args:
            action_type: アクション型 IRI。
            target: 対象実体の IRI。
            window: 時間窓。
            at: 判定時刻。
        """
        moment = ensure_utc(at)
        history = self.executions.get((action_type, target), [])
        return sum(1 for executed in history if moment - executed < window)


@dataclass(slots=True)
class ActionRequest:
    """1 回のアクション実行要求。段階を進めるメソッドしか持たない。

    Attributes:
        contract: 実行する契約。
        target: 対象実体の IRI。
        requested_by: 要求した主体の IRI。
        severity: 起因となった不一致の深刻度（承認判定に使う）。
        stage: 現在の段階。
        command_id: 冪等の鍵。
        correlation_id: 起因の相関 ID。
        report: 前提検証の結果。
        approval: 承認。
        override: 上書き。
        event: 発行した ``ActionEvent``。
    """

    contract: ActionContract
    target: str
    requested_by: str
    severity: float = 0.0
    stage: ActionStage = ActionStage.REQUESTED
    command_id: UUID = field(default_factory=uuid4)
    correlation_id: UUID | None = None
    report: PreconditionReport | None = None
    approval: Approval | None = None
    override: Override | None = None
    event: ActionEvent | None = None

    def check(self, report: PreconditionReport, *, override: Override | None = None) -> ActionStage:
        """前提検証の結果を受け取る（設計書 §9.2 の 1）。

        満たしていなければ ``override`` が無い限り ``REJECTED`` に進む。

        Args:
            report: 三面の判定結果。
            override: 前提未達を上書きする場合の記録。

        Returns:
            進んだ後の段階。

        Raises:
            ActionError: 段階が ``REQUESTED`` でない、または報告の対象が食い違う場合。
        """
        self._require(ActionStage.CHECKED)
        if report.action_type != self.contract.action_type or report.target != self.target:
            raise ActionError(
                f"precondition report is for {report.action_type}@{report.target} but this "
                f"request is {self.contract.action_type}@{self.target}"
            )
        self.report = report
        if report.satisfied:
            self.stage = ActionStage.CHECKED
            return self.stage
        if override is None:
            self.stage = ActionStage.REJECTED
            log.info(
                "actions.rejected_preconditions",
                action_type=self.contract.action_type,
                target=self.target,
                failed_faces=list(report.failed_faces()),
            )
            return self.stage
        self.override = override
        self.stage = ActionStage.CHECKED
        log.info(
            "actions.preconditions_overridden",
            action_type=self.contract.action_type,
            target=self.target,
            overridden_by=override.overridden_by,
            reason=override.reason,
            failed_faces=list(override.failed_faces),
        )
        return self.stage

    def approve(self, approval: Approval | None = None) -> ActionStage:
        """承認を受け取る（設計書 §9.2 の 2）。

        ``risk_class`` が ``medium`` 以上なら承認は必須で、``None`` を渡すと例外になる。

        Args:
            approval: 承認。承認不要な場合は ``None``。

        Returns:
            進んだ後の段階。

        Raises:
            ApprovalRequiredError: 承認が必要なのに無い、またはロールが違う場合。
            ActionError: 段階が ``CHECKED`` でない場合。
        """
        self._require(ActionStage.APPROVED)
        needed = self.contract.requires_approval(severity=self.severity)
        if needed and approval is None:
            raise ApprovalRequiredError(
                f"{self.contract.action_type} is risk class {self.contract.risk_class.value} and "
                f"needs approval from {self.contract.approvals.role} (design §9.3)"
            )
        if approval is not None and approval.role != self.contract.approvals.role:
            raise ApprovalRequiredError(
                f"{self.contract.action_type} must be approved by "
                f"{self.contract.approvals.role}, not {approval.role}"
            )
        self.approval = approval
        self.stage = ActionStage.APPROVED
        return self.stage

    def execute(
        self,
        *,
        at: datetime,
        executed_by: str,
        rate_limits: RateLimitState,
    ) -> ActionEvent:
        """実行して ``ActionEvent`` を発行する（設計書 §9.2 の 3）。

        Args:
            at: 実行時刻。
            executed_by: 実行した主体の IRI。
            rate_limits: 対象ごとの実行履歴。

        Returns:
            発行した ``ActionEvent``。

        Raises:
            ActionError: 段階が ``APPROVED`` でない、またはレート制限に当たる場合。
        """
        self._require(ActionStage.EXECUTED)
        limit = self.contract.rate_limit
        if limit is not None:
            recent = rate_limits.count_within(
                action_type=self.contract.action_type,
                target=self.target,
                window=limit.window,
                at=at,
            )
            if recent >= limit.per_target_max:
                self.stage = ActionStage.REJECTED
                raise ActionError(
                    f"{self.contract.action_type} on {self.target} is rate limited: "
                    f"{recent} execution(s) within {limit.per_target_window} "
                    f"(max {limit.per_target_max}, design §9.3)"
                )

        assert self.report is not None  # check() が通っていれば必ずある
        event = ActionEvent(
            event_id=new_event_id(),
            action_type=self.contract.action_type,
            target=self.target,
            command_id=self.command_id,
            executed_at=ensure_utc(at),
            executed_by=executed_by,
            approval=self.approval,
            override=self.override,
            precondition_note=self.report.explain(),
            correlation_id=self.correlation_id,
        )
        rate_limits.record(action_type=self.contract.action_type, target=self.target, at=at)
        self.event = event
        self.stage = ActionStage.EXECUTED
        log.info(
            "actions.executed",
            action_type=self.contract.action_type,
            target=self.target,
            event_id=str(event.event_id),
            command_id=str(self.command_id),
            overridden=event.was_overridden,
            approved_by=None if self.approval is None else self.approval.approved_by,
        )
        return event

    def compensate(self, *, at: datetime, reason: str) -> ActionStage:
        """補償を記録する（実行の取り消し）。

        補償アクション**そのものの実行**は別の :class:`ActionRequest` として行う（補償にも
        前提と承認がある）。ここが記録するのは「この実行を取り消すことにした」事実である。

        Args:
            at: 補償時刻。
            reason: 補償の理由。

        Returns:
            進んだ後の段階。

        Raises:
            ActionError: 段階が ``EXECUTED`` でない、または理由が空の場合。
        """
        self._require(ActionStage.COMPENSATED)
        if not reason:
            raise ActionError("compensation must state its reason")
        self.stage = ActionStage.COMPENSATED
        log.info(
            "actions.compensated",
            action_type=self.contract.action_type,
            target=self.target,
            compensation=self.contract.compensation,
            at=ensure_utc(at).isoformat(),
            reason=reason,
        )
        return self.stage

    def _require(self, to: ActionStage) -> None:
        """段階の遷移が許可されているか検証する。"""
        if to not in _ALLOWED[self.stage]:
            allowed = sorted(stage.value for stage in _ALLOWED[self.stage])
            raise ActionError(
                f"cannot move {self.contract.action_type}@{self.target} from "
                f"{self.stage.value} to {to.value}; allowed: {allowed or ['(terminal)']}"
            )


def require_satisfied(report: PreconditionReport) -> None:
    """前提が満たされていなければ例外にする（上書きを使わない呼び出し用）。

    Args:
        report: 三面の判定結果。

    Raises:
        PreconditionNotMetError: 満たしていない場合。落ちた面と理由を挙げる。
    """
    if not report.satisfied:
        raise PreconditionNotMetError(report.explain())


def planned_expectation_deadlines(
    contract: ActionContract, *, executed_at: datetime
) -> tuple[tuple[str, datetime, str], ...]:
    """効果ごとの期限を計算する（設計書 §9.2 の 4 の入力）。

    Args:
        contract: 実行した契約。
        executed_at: 実行時刻。

    Returns:
        ``(効果の説明, 期限, 由来)`` の列。由来は ``symbolic`` / ``rollout``。
    """
    moment = ensure_utc(executed_at)
    return tuple(
        (effect.description, moment + effect.deadline, effect.origin)
        for effect in contract.all_effects
    )
