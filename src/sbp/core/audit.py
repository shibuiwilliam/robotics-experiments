"""監査隔離（不変条件 6、設計書 §4.4、付録 C）。

**なぜ ``core`` にあるのか。** マスクは層をまたぐ契約である。記録尤度の計算（``reasoning``）と
記号条件付けベクトルの生成（``worldmodel``）の両方が必須引数として取るが、この 2 つは依存の
向きが違う層にある（``worldmodel`` は ``reasoning`` を import できない）。CLAUDE.md の
「層をまたぐ結合は ``core`` の型と ``eventlog`` のイベントだけ」に従い、ここに置く。

**原則**: ある記録を監査する推定に、その記録自身と派生信念を入力しない。

なぜこれが必要か。SBP は「業務記録が正しいか」を物理観測から判断する。もし判断に使う推定が
その記録を入力に含んでいたら、記録が推定を動かし、動いた推定が記録を支持する——循環である。
設計書 §15 が挙げる失敗モード「記録パスと推定パスの循環汚染」そのもので、兆候は「記録訂正後に
不一致が消えすぎる」こと。

**実装の要点は「マスクなしで呼べる経路を作らない」こと。** 後から差し込む設計にすると、
マスクを忘れた呼び出しが残る。そのため:

- 記録尤度の計算は :class:`AuditMask` を**必須の引数**として取る。
- 記号条件付けベクトルの生成（``worldmodel.context.build``）も同様。
- マスクを通した結果は :class:`MaskedEvidence` にまとめ、「どれを除外したか」を保持する。
  除外の事実が来歴に残らなければ、監査隔離が働いたことを後から確認できない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from sbp.core.errors import AuditIsolationError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sbp.core.events import EventEnvelope
    from sbp.core.records import Record

__all__ = ["AuditMask", "MaskedEvidence", "no_audit"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AuditMask:
    """監査対象を推定の入力から除外するマスク（不変条件 6）。

    「いま何を監査しているか」を明示的に持つ。空のマスク（:func:`no_audit`）は「何も監査して
    いない」ことを意味し、そのときは全証拠を使ってよい——ただし**空であることも明示的に
    作らせる**（既定引数で省略できるようにしない）。

    Attributes:
        audited_records: 監査対象の記録 ID。これら自身を入力にしない。
        audited_assertions: 監査対象の記録から派生したアサーション ID。間接的な混入も防ぐ。
        audited_sources: 監査対象の情報源 IRI。情報源まるごとを監査する場合に使う
            （「MES の実績全体が疑わしい」という状況）。
        reason: 何を監査しているのか（来歴に残す）。
    """

    audited_records: frozenset[UUID] = frozenset()
    audited_assertions: frozenset[UUID] = frozenset()
    audited_sources: frozenset[str] = frozenset()
    reason: str = ""

    @classmethod
    def auditing_record(cls, record: Record, *, reason: str) -> AuditMask:
        """1 件の記録を監査するマスクを作る。

        Args:
            record: 監査対象の記録。
            reason: 監査の理由（不一致 ID など）。
        """
        if not reason:
            raise AuditIsolationError(
                "an audit mask must state what it is auditing; the reason goes into provenance "
                "so that the isolation can be verified afterwards (invariant 6)"
            )
        return cls(audited_records=frozenset({record.record_id}), reason=reason)

    @classmethod
    def auditing_source(cls, source: str, *, reason: str) -> AuditMask:
        """情報源まるごとを監査するマスクを作る。

        Args:
            source: 監査対象の情報源 IRI。
            reason: 監査の理由。
        """
        if not reason:
            raise AuditIsolationError("an audit mask must state what it is auditing")
        return cls(audited_sources=frozenset({source}), reason=reason)

    @property
    def is_empty(self) -> bool:
        """何も監査していないマスクかどうか。"""
        return not (self.audited_records or self.audited_assertions or self.audited_sources)

    def excludes_record(self, record: Record) -> bool:
        """この記録を除外すべきか。

        Args:
            record: 判定する記録。
        """
        return record.record_id in self.audited_records or record.source in self.audited_sources

    def excludes_assertion(self, assertion_id: UUID) -> bool:
        """このアサーションを除外すべきか。

        Args:
            assertion_id: 判定するアサーション ID。
        """
        return assertion_id in self.audited_assertions

    def excludes_event(self, event: EventEnvelope) -> bool:
        """このイベントを除外すべきか。

        記録由来のイベント（``RecordEvidence`` を持つもの）について、その記録または情報源が
        監査対象なら除外する。プローブ由来のイベントは物理観測であり、記録の監査では除外しない
        ——それが「物理から記録を監査する」ことの中身である。

        Args:
            event: 判定するイベント。
        """
        for evidence in event.evidence:
            if evidence.kind != "record":
                continue
            if evidence.source in self.audited_sources:
                return True
            # 記録 IRI に記録 ID が含まれる形（site_iri(..., "record", <uuid>)）を想定
            for record_id in self.audited_records:
                if str(record_id) in evidence.record:
                    return True
        return False

    def merge(self, other: AuditMask) -> AuditMask:
        """2 つのマスクを合わせる（両方の監査対象を除外する）。

        Args:
            other: 合わせるマスク。
        """
        reasons = [r for r in (self.reason, other.reason) if r]
        return AuditMask(
            audited_records=self.audited_records | other.audited_records,
            audited_assertions=self.audited_assertions | other.audited_assertions,
            audited_sources=self.audited_sources | other.audited_sources,
            reason="; ".join(reasons),
        )

    def filter_records(self, records: Sequence[Record]) -> MaskedEvidence[Record]:
        """記録の列からマスク対象を除く。

        Args:
            records: 候補の記録。

        Returns:
            採用した記録と除外した記録の両方（除外の事実を来歴に残すため）。
        """
        kept: list[Record] = []
        excluded: list[Record] = []
        for record in records:
            (excluded if self.excludes_record(record) else kept).append(record)
        if excluded:
            log.info(
                "reasoning.audit_isolation_applied",
                excluded=len(excluded),
                kept=len(kept),
                reason=self.reason,
            )
        return MaskedEvidence(kept=tuple(kept), excluded=tuple(excluded), mask=self)

    def filter_events(self, events: Sequence[EventEnvelope]) -> MaskedEvidence[EventEnvelope]:
        """イベントの列からマスク対象を除く。

        Args:
            events: 候補のイベント。
        """
        kept: list[EventEnvelope] = []
        excluded: list[EventEnvelope] = []
        for event in events:
            (excluded if self.excludes_event(event) else kept).append(event)
        if excluded:
            log.info(
                "reasoning.audit_isolation_applied",
                excluded=len(excluded),
                kept=len(kept),
                reason=self.reason,
            )
        return MaskedEvidence(kept=tuple(kept), excluded=tuple(excluded), mask=self)

    def provenance_note(self) -> str:
        """来歴に残す文字列。

        監査隔離が働いたことを後から確認できるようにする。空のマスクでも「隔離なし」と
        明示する——記録が無いと「マスクを忘れた」のか「隔離が不要だった」のか区別できない。
        """
        if self.is_empty:
            return "audit-isolation: none (no record under audit)"
        parts: list[str] = []
        if self.audited_records:
            parts.append(f"records={len(self.audited_records)}")
        if self.audited_assertions:
            parts.append(f"assertions={len(self.audited_assertions)}")
        if self.audited_sources:
            parts.append(f"sources={sorted(self.audited_sources)}")
        return f"audit-isolation: excluded {', '.join(parts)} (reason: {self.reason})"


@dataclass(frozen=True, slots=True)
class MaskedEvidence[T]:
    """マスクを通した証拠。

    除外したものも保持するのが要点である。「何を除外したか」が来歴に残らなければ、監査隔離が
    働いたことを後から検証できない（設計書の監査性要件）。

    Attributes:
        kept: 推定に使う証拠。
        excluded: 監査隔離で除外した証拠。
        mask: 適用したマスク。
    """

    kept: tuple[T, ...]
    excluded: tuple[T, ...]
    mask: AuditMask = field(default_factory=AuditMask)

    @property
    def exclusion_count(self) -> int:
        """除外した件数。"""
        return len(self.excluded)

    @property
    def is_isolated(self) -> bool:
        """実際に隔離が働いたかどうか。"""
        return bool(self.excluded)

    def __len__(self) -> int:
        """採用した証拠の件数。"""
        return len(self.kept)

    def __iter__(self) -> Iterable[T]:
        """採用した証拠を走査する。"""
        return iter(self.kept)


def no_audit() -> AuditMask:
    """何も監査していないマスク。

    「監査対象が無い」ことを明示的に作らせるための関数。信念更新や条件付けの呼び出しで
    ``mask`` を省略できるようにすると、監査文脈で呼ぶときにマスクを忘れる経路が生まれる。
    常に明示的に渡すことで、呼び出し側が「いま監査しているか」を意識せざるを得なくなる。
    """
    return AuditMask()
