"""業務システムへの書き戻し（設計書 §7.6、integration ルール）。

既定は ``SBP_WRITEBACK_MODE=dry``。**dry が既定であることが安全設計の中心**で、設定を
明示的に live にしない限り業務システムは変更されない。

要点 3 つ:

1. **冪等** — コマンドは ``command_id`` を持ち、再送しても二重に適用されない。
2. **応答を取り込む** — 業務システムの応答は ``BusinessEvent`` として戻し、アクションの
   ``ActionEvent`` と因果 ID で結ぶ。書き戻して終わりにしない（効果の検証に必要）。
3. **補償を必ず定義** — 補償アクションが無いコマンドは**作れない**（型レベルで必須）。
   取り消せない書き戻しを基盤から出さないため。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID, uuid4

from sbp.core.config import WritebackMode
from sbp.core.errors import WritebackError
from sbp.core.logging import get_logger
from sbp.core.time import now

if TYPE_CHECKING:
    from datetime import datetime

__all__ = [
    "WritebackCommand",
    "WritebackExecutor",
    "WritebackOutcome",
    "WritebackResult",
    "WritebackTarget",
]

log = get_logger(__name__)


class WritebackResult(StrEnum):
    """書き戻しの結果。"""

    PLANNED = "planned"
    """dry モードで「こうする予定」を示しただけ。業務システムは変更されていない。"""
    APPLIED = "applied"
    """live モードで適用された。"""
    DUPLICATE = "duplicate"
    """同じ ``command_id`` が既に適用済みだったため何もしなかった（冪等）。"""
    FAILED = "failed"
    """失敗した。補償の対象になりうる。"""


@dataclass(frozen=True, slots=True)
class WritebackTarget:
    """書き戻しの宛先。

    Attributes:
        source: 情報源 IRI（どの業務システムか）。
        table: テーブル・エンティティ名。
        external_id: 業務システム側の主キー。写像の ``key.external_id`` の逆向きに対応する
            （ADR-0006 の未決事項 ``Δ_F`` に相当）。
    """

    source: str
    table: str
    external_id: str


@dataclass(frozen=True, slots=True)
class WritebackCommand:
    """業務システムへのコマンド。

    ``compensation`` を必須にしているのが要点である。既定値を持たせず、省略すると構築に
    失敗する。取り消せない書き戻しを基盤から出さないため（設計書 §7.6、§9.1）。

    Attributes:
        target: 宛先。
        changes: 変更内容（列名 → 新しい値）。
        compensation: この変更を取り消すコマンド型の IRI。**必須**。
        reason: なぜこの書き戻しを行うのか（人が読む。来歴に残す）。
        command_id: 冪等の鍵。再送しても二重適用されない。
        issued_at: 発行時刻。
        causation_id: 原因となったイベント（不一致の起票など）の ``event_id``。
    """

    target: WritebackTarget
    changes: dict[str, Any]
    compensation: str
    reason: str
    command_id: UUID = field(default_factory=uuid4)
    issued_at: datetime = field(default_factory=now)
    causation_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.compensation:
            raise WritebackError(
                "a writeback command must declare its compensation; "
                "commands that cannot be undone are not issued (design §7.6)"
            )
        if not self.changes:
            raise WritebackError("a writeback command must change at least one field")
        if not self.reason:
            raise WritebackError(
                "a writeback command must carry a human-readable reason "
                "(it ends up in provenance and in the business system's audit trail)"
            )


@dataclass(frozen=True, slots=True)
class WritebackOutcome:
    """書き戻しの結果。

    Attributes:
        command: 実行したコマンド。
        result: 結果。
        detail: 業務システムの応答や失敗理由。
        applied_at: 適用時刻（``dry`` では ``None``）。
    """

    command: WritebackCommand
    result: WritebackResult
    detail: str = ""
    applied_at: datetime | None = None

    @property
    def changed_the_business_system(self) -> bool:
        """業務システムが実際に変更されたか。"""
        return self.result is WritebackResult.APPLIED


@runtime_checkable
class BusinessSystemWriter(Protocol):
    """業務システムへの書き込みの抽象（モックと実システムで共通）。"""

    def write(self, target: WritebackTarget, changes: dict[str, Any]) -> str:
        """変更を適用して応答を返す。

        Args:
            target: 宛先。
            changes: 変更内容。

        Returns:
            業務システムの応答（説明用の文字列）。
        """
        ...


class WritebackExecutor:
    """書き戻しの実行者。

    ``dry`` では**書き込みを試みない**（writer を呼ばない）。「dry のつもりで実際に書いた」
    という事故を構造的に防ぐため、モード判定を writer 呼び出しより手前に置く。
    """

    def __init__(
        self,
        *,
        mode: WritebackMode = WritebackMode.DRY,
        writer: BusinessSystemWriter | None = None,
    ) -> None:
        """Args:
        mode: ``dry`` なら書き込まない（既定）。``live`` のときだけ writer を呼ぶ。
        writer: 実際に書き込む相手。``live`` では必須。

        Raises:
            WritebackError: ``live`` なのに writer が無い場合。
        """
        if mode is WritebackMode.LIVE and writer is None:
            raise WritebackError("live writeback requires a writer")
        self._mode = mode
        self._writer = writer
        self._applied: dict[UUID, WritebackOutcome] = {}

    @property
    def mode(self) -> WritebackMode:
        """現在のモード。"""
        return self._mode

    def execute(self, command: WritebackCommand) -> WritebackOutcome:
        """コマンドを実行する（``dry`` なら計画を返すだけ）。

        Args:
            command: 実行するコマンド。

        Returns:
            結果。``dry`` では常に :attr:`WritebackResult.PLANNED`。
        """
        bound = log.bind(
            command_id=str(command.command_id),
            target=f"{command.target.table}/{command.target.external_id}",
            mode=self._mode.value,
            causation_id=None if command.causation_id is None else str(command.causation_id),
        )

        # 冪等: 同じ command_id は 1 度だけ適用する（不変条件 4 と同じ考え方）
        existing = self._applied.get(command.command_id)
        if existing is not None:
            bound.info("writeback.duplicate", previous_result=existing.result.value)
            return WritebackOutcome(
                command=command,
                result=WritebackResult.DUPLICATE,
                detail=f"already {existing.result.value} at {existing.applied_at}",
                applied_at=existing.applied_at,
            )

        if self._mode is WritebackMode.DRY:
            bound.info(
                "writeback.dry_run",
                changes=sorted(command.changes),
                reason=command.reason,
                compensation=command.compensation,
            )
            outcome = WritebackOutcome(
                command=command,
                result=WritebackResult.PLANNED,
                detail=(
                    "dry run: the business system was not modified. "
                    f"Would set {sorted(command.changes)} on "
                    f"{command.target.table}/{command.target.external_id}."
                ),
            )
            self._applied[command.command_id] = outcome
            return outcome

        assert self._writer is not None
        try:
            response = self._writer.write(command.target, command.changes)
        except Exception as exc:
            bound.error("writeback.failed", error=str(exc))
            # 失敗は記録するが、再試行できるよう冪等記録には入れない。
            return WritebackOutcome(command=command, result=WritebackResult.FAILED, detail=str(exc))

        applied_at = now()
        bound.info("writeback.applied", response=response[:200])
        outcome = WritebackOutcome(
            command=command,
            result=WritebackResult.APPLIED,
            detail=response,
            applied_at=applied_at,
        )
        self._applied[command.command_id] = outcome
        return outcome

    def compensate(self, outcome: WritebackOutcome, *, changes: dict[str, Any]) -> WritebackOutcome:
        """適用済みの書き戻しを補償する（取り消す）。

        Args:
            outcome: 取り消す対象の結果。
            changes: 元に戻すための変更内容。

        Returns:
            補償コマンドの結果。

        Raises:
            WritebackError: 適用されていない書き戻しを補償しようとした場合。
        """
        if not outcome.changed_the_business_system:
            raise WritebackError(
                f"cannot compensate a writeback that was {outcome.result.value}; "
                "only applied changes need undoing"
            )
        compensating = WritebackCommand(
            target=outcome.command.target,
            changes=changes,
            # 補償の補償は元のコマンド型（取り消しを取り消すと元に戻る）
            compensation=outcome.command.compensation,
            reason=f"compensation for command {outcome.command.command_id}",
            causation_id=outcome.command.causation_id,
        )
        return self.execute(compensating)
