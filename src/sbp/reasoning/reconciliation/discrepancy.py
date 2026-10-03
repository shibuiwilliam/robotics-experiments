"""不一致オブジェクトと状態機械（設計書 §8.2、付録 A.2）。

中核テーゼ 5「不一致は例外ではなく製品」の実装。不一致は**一級オブジェクト**であり、検出して
ログに流すものではなく、起票され、担当が付き、解決され、**学習信号になって消える**。

状態機械は次の遷移だけを許す（設計書 §8.2 のライフサイクル図）::

    Open → Triaged → Assigned → Resolved → Learned
    Open → Suppressed

``status`` を直接書き換えられないように :class:`Discrepancy` は frozen であり、遷移は
:meth:`Discrepancy.transition` 経由のみ。各遷移には**その状態で必要な情報が揃っていること**を
要求する（担当なしで ``Assigned``、解決結果なしで ``Resolved`` にできない）。状態名だけ進んで
中身が空のチケットは、運用では「対応済み」に見えて実際は放置されたものになる。

``Suppressed`` は信頼性モデルで説明できる系統誤差に限る。理由と根拠（どの ``SystematicBias``
で説明したか）を必須にしているのは、抑制が「不都合な不一致を消す手段」に転用されるのを防ぐ
ためである（設計書 §8.2「抑制も来歴に残す」）。
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbp.core.errors import DiscrepancyTransitionError
from sbp.core.logging import get_logger
from sbp.core.time import Interval, UtcDatetime
from sbp.reasoning.reconciliation.severity import Severity

__all__ = [
    "Discrepancy",
    "DiscrepancyKind",
    "DiscrepancyStatus",
    "LearningSignals",
    "Resolution",
    "ResolutionOutcome",
    "StatusChange",
    "Suppression",
]

log = get_logger(__name__)


class DiscrepancyKind(IntEnum):
    """五類型（設計書 §8.2）。番号はオントロジーの ``sbp:discrepancyKind`` と一致させる。"""

    EXISTENCE = 1
    """存在。あるはずのものが記録に無い、記録にあるものが物理に無い。"""
    STATE = 2
    """状態。物理観測と業務記録が別の状態を主張している。"""
    TIME = 3
    """時間。発生時刻と記録時刻のずれが許容プロファイルを超える、または順序制約違反。"""
    IDENTITY = 4
    """同一性。束縛の撤回、競合する同一性主張。"""
    NORM = 5
    """規範。規程・契約・安全規則の違反。"""


class DiscrepancyStatus(StrEnum):
    """不一致のライフサイクル状態（設計書 §8.2）。"""

    OPEN = "Open"
    """検出規則が発火した直後。"""
    TRIAGED = "Triaged"
    """類型・深刻度を確定し、重複を統合した。"""
    ASSIGNED = "Assigned"
    """担当ロールへ配賦した。"""
    RESOLVED = "Resolved"
    """解決結果を記録した。"""
    LEARNED = "Learned"
    """学習信号に変換した（終端）。"""
    SUPPRESSED = "Suppressed"
    """既知の系統誤差として抑制した（終端）。"""

    @property
    def is_terminal(self) -> bool:
        """終端状態かどうか。"""
        return self in {DiscrepancyStatus.LEARNED, DiscrepancyStatus.SUPPRESSED}


_ALLOWED: dict[DiscrepancyStatus, frozenset[DiscrepancyStatus]] = {
    DiscrepancyStatus.OPEN: frozenset({DiscrepancyStatus.TRIAGED, DiscrepancyStatus.SUPPRESSED}),
    DiscrepancyStatus.TRIAGED: frozenset({DiscrepancyStatus.ASSIGNED}),
    DiscrepancyStatus.ASSIGNED: frozenset({DiscrepancyStatus.RESOLVED}),
    DiscrepancyStatus.RESOLVED: frozenset({DiscrepancyStatus.LEARNED}),
    DiscrepancyStatus.LEARNED: frozenset(),
    DiscrepancyStatus.SUPPRESSED: frozenset(),
}
"""許可された遷移。ここに無い遷移は :class:`DiscrepancyTransitionError`。"""


class ResolutionOutcome(StrEnum):
    """解決結果（設計書 §8.2 の「解決結果 → 学習信号」表の行）。"""

    PHYSICAL_CORRECT = "physical_correct"
    """物理が正しい → 記録訂正提案と信頼性モデル更新。"""
    RECORD_CORRECT = "record_correct"
    """記録が正しい → プローブ／WM の負例バッグ。"""
    BOTH_WRONG = "both_wrong"
    """両方誤り → オントロジー欠落候補。"""
    UNDETERMINED = "undetermined"
    """判定不能 → 追加観測要求。"""


class Resolution(BaseModel):
    """解決の記録（付録 A.2 の ``resolution``）。

    Attributes:
        outcome: 解決結果。
        note: 判断の根拠（人が書く）。
        resolved_by: 解決した主体の IRI（人・ロール・自動処理）。
        resolved_at: 解決時刻。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: ResolutionOutcome
    note: str
    resolved_by: str
    resolved_at: UtcDatetime


class LearningSignals(BaseModel):
    """学習信号（付録 A.2 の ``learning``）。

    付録 A.2 の ``learning`` に ``corrections_proposed`` を加えている（加法的な拡張）。
    設計書 §8.2 の表は「物理が正しい → 記録訂正（書き戻し提案）」を第一の処置に挙げており、
    **訂正提案は学習信号である**。これを数えないと、訂正提案だけを生んだ解決が「何も学ばなかった」
    ことになり ``Learned`` に進めない（実際に G5 の統合テストがこれで落ちた）。

    Attributes:
        labels_emitted: 生成した弱ラベル（``TimeBag`` など）の IRI。
        reliability_updates: 更新した信頼性モデルの鍵。
        concept_candidates: 提案した概念候補の IRI。
        observation_requests: エピステミック・スケジューラへ積んだ観測要求の IRI。
        corrections_proposed: 業務システムへの訂正提案（``command_id``）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    labels_emitted: tuple[str, ...] = ()
    reliability_updates: tuple[str, ...] = ()
    concept_candidates: tuple[str, ...] = ()
    observation_requests: tuple[str, ...] = ()
    corrections_proposed: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """何の学習信号も生まなかったか。"""
        return not (
            self.labels_emitted
            or self.reliability_updates
            or self.concept_candidates
            or self.observation_requests
            or self.corrections_proposed
        )


class Suppression(BaseModel):
    """抑制の記録（設計書 §8.2「抑制も来歴に残す」）。

    Attributes:
        bias_kind: 説明に使った系統誤差の種別（``SystematicBias.kind``）。
        reliability_key: 根拠にした信頼性モデルの鍵（``source|record_type``）。
        reason: 抑制の理由（人が読む文）。
        suppressed_at: 抑制時刻。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bias_kind: str = Field(min_length=1)
    reliability_key: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    suppressed_at: UtcDatetime


class StatusChange(BaseModel):
    """状態遷移 1 回分の記録（来歴）。

    Attributes:
        from_status: 遷移前の状態。
        to_status: 遷移後の状態。
        at: 遷移時刻。
        note: 遷移の理由。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_status: DiscrepancyStatus
    to_status: DiscrepancyStatus
    at: UtcDatetime
    note: str = ""


class Discrepancy(BaseModel):
    """物理と業務の食い違い（付録 A.2）。

    Attributes:
        discrepancy_id: 識別子。
        kind: 五類型のいずれか。
        subjects: 対象の実体 IRI（複数あるのは類型 4 の競合クレームなど）。
        assertions: 根拠になった信念アサーションの IRI。
        evidence: 根拠になった証拠（イベント・記録）の IRI。
        detected_at: 検出時刻。
        interval: 不一致が成立していた区間（右開）。
        severity: 深刻度。
        status: ライフサイクル状態。
        assignee_role: 配賦先ロールの IRI。
        resolution: 解決の記録。
        learning: 学習信号。
        suppression: 抑制の記録。
        history: 状態遷移の履歴。
        detector: 検出した規則の名前（``kind1`` など）。
        ontology_version: 検出時のオントロジー版。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    discrepancy_id: UUID = Field(default_factory=uuid4)
    kind: DiscrepancyKind
    subjects: tuple[str, ...] = Field(min_length=1)
    assertions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    detected_at: UtcDatetime
    interval: Interval
    severity: Severity
    status: DiscrepancyStatus = DiscrepancyStatus.OPEN
    assignee_role: str | None = None
    resolution: Resolution | None = None
    learning: LearningSignals | None = None
    suppression: Suppression | None = None
    history: tuple[StatusChange, ...] = ()
    detector: str = Field(min_length=1)
    ontology_version: str

    @model_validator(mode="after")
    def _status_matches_its_payload(self) -> Self:
        """状態と中身の整合を検証する（状態名だけ進んだチケットを作れないようにする）。"""
        if self.status is DiscrepancyStatus.ASSIGNED and not self.assignee_role:
            raise DiscrepancyTransitionError("an Assigned discrepancy must name an assignee_role")
        if (
            self.status in {DiscrepancyStatus.RESOLVED, DiscrepancyStatus.LEARNED}
            and self.resolution is None
        ):
            raise DiscrepancyTransitionError(
                f"a {self.status.value} discrepancy must carry its resolution"
            )
        if self.status is DiscrepancyStatus.LEARNED and self.learning is None:
            raise DiscrepancyTransitionError(
                "a Learned discrepancy must carry the learning signals it produced"
            )
        if self.status is DiscrepancyStatus.SUPPRESSED and self.suppression is None:
            raise DiscrepancyTransitionError(
                "a Suppressed discrepancy must record why it was suppressed and which "
                "systematic bias explains it (design §8.2)"
            )
        return self

    @property
    def merge_key(self) -> tuple[DiscrepancyKind, tuple[str, ...]]:
        """重複統合の鍵（同一実体・同一類型）。区間の重なりは別に判定する。"""
        return (self.kind, tuple(sorted(self.subjects)))

    @property
    def is_actionable(self) -> bool:
        """まだ人の手を必要とする状態か。"""
        return not self.status.is_terminal

    def transition(
        self,
        to: DiscrepancyStatus,
        *,
        at: datetime,
        note: str = "",
        assignee_role: str | None = None,
        resolution: Resolution | None = None,
        learning: LearningSignals | None = None,
        suppression: Suppression | None = None,
    ) -> Discrepancy:
        """状態を進める（許可された遷移のみ）。

        Args:
            to: 遷移先の状態。
            at: 遷移時刻。
            note: 遷移の理由（来歴に残す）。
            assignee_role: ``Assigned`` へ進むときの担当ロール IRI。
            resolution: ``Resolved`` へ進むときの解決記録。
            learning: ``Learned`` へ進むときの学習信号。
            suppression: ``Suppressed`` へ進むときの抑制記録。

        Returns:
            新しい状態の不一致（元のオブジェクトは変えない）。

        Raises:
            DiscrepancyTransitionError: 許可されていない遷移、または必要な情報が欠けている場合。
        """
        if to not in _ALLOWED[self.status]:
            allowed = sorted(status.value for status in _ALLOWED[self.status])
            raise DiscrepancyTransitionError(
                f"cannot move discrepancy {self.discrepancy_id} from {self.status.value} to "
                f"{to.value}; allowed: {allowed or ['(terminal)']}"
            )
        if to is DiscrepancyStatus.LEARNED and learning is not None and learning.is_empty:
            raise DiscrepancyTransitionError(
                "moving to Learned requires at least one learning signal; a resolution that "
                "teaches nothing should stay Resolved (design §8.2)"
            )
        change = StatusChange(from_status=self.status, to_status=to, at=at, note=note)
        updated = self.model_copy(
            update={
                "status": to,
                "assignee_role": assignee_role or self.assignee_role,
                "resolution": resolution or self.resolution,
                "learning": learning or self.learning,
                "suppression": suppression or self.suppression,
                "history": (*self.history, change),
            }
        )
        # frozen なので model_copy は検証を走らせない。整合は明示的に再検証する
        validated = Discrepancy.model_validate(updated.model_dump())
        log.info(
            "reconciliation.discrepancy_transition",
            discrepancy=str(self.discrepancy_id),
            kind=int(self.kind),
            from_status=self.status.value,
            to_status=to.value,
        )
        return validated

    def merge(self, other: Discrepancy, *, at: datetime) -> Discrepancy:
        """同一実体・同一類型・区間の重なる不一致を 1 件に束ねる（設計書 §8.2）。

        証拠と根拠アサーションを足し合わせ、区間を包含区間に広げ、深刻度は高い方を採る。
        統合される側を捨てずに証拠として取り込むのが要点で、「何件の観測が同じことを言って
        いたか」が解決時の判断材料になる。

        Args:
            other: 統合する不一致。
            at: 統合時刻。

        Returns:
            統合後の不一致。

        Raises:
            DiscrepancyTransitionError: 鍵が違う、区間が重ならない、または終端状態の場合。
        """
        if self.merge_key != other.merge_key:
            raise DiscrepancyTransitionError(
                f"cannot merge discrepancies with different keys: {self.merge_key} and "
                f"{other.merge_key}"
            )
        if not self.interval.overlaps(other.interval):
            raise DiscrepancyTransitionError(
                "cannot merge discrepancies whose intervals do not overlap "
                f"({self.interval.start.isoformat()}.. and {other.interval.start.isoformat()}..)"
            )
        if self.status.is_terminal or other.status.is_terminal:
            raise DiscrepancyTransitionError(
                "cannot merge a discrepancy that has already reached a terminal state"
            )
        end: datetime | None = (
            None
            if self.interval.end is None or other.interval.end is None
            else max(self.interval.end, other.interval.end)
        )
        merged = self.model_copy(
            update={
                "interval": Interval(min(self.interval.start, other.interval.start), end),
                "assertions": tuple(dict.fromkeys((*self.assertions, *other.assertions))),
                "evidence": tuple(dict.fromkeys((*self.evidence, *other.evidence))),
                "severity": max(
                    (self.severity, other.severity), key=lambda severity: severity.score
                ),
                "history": (
                    *self.history,
                    StatusChange(
                        from_status=self.status,
                        to_status=self.status,
                        at=at,
                        note=f"merged {other.discrepancy_id}",
                    ),
                ),
            }
        )
        log.info(
            "reconciliation.discrepancy_merged",
            discrepancy=str(self.discrepancy_id),
            absorbed=str(other.discrepancy_id),
            kind=int(self.kind),
        )
        return merged
