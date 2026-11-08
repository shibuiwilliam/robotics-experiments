"""隔離キュー（M10-01、ADR-0035。設計書 §3.9 / §5.7）。

構造が壊れた入力（写像に失敗した行、型がオントロジーに無い台帳の行、SHACL の ``sh:Violation``）を、
**登録せず・捨てず**に人の確認へ回す作業キュー。信念ではないので推定の入力にしない（不変条件 6）。

- 識別子は ``(拠点, 情報源, 表, 主語, 理由)`` から決める。台帳は毎周期全行を読むので、同じ行が同じ
  理由で落ち続けても 1 行にしかならない。理由が変われば別の行になる。
- 確認済みの行は、同じ理由で再び落ちても開き直さない（人が「対応不要」と決めたものを積み直さない）。
- 行を消さない。確認の記録（``reviewed_at`` / ``resolution``）を立てるだけ。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from psycopg import AsyncConnection
__all__ = [
    "QuarantineReviewCommand",
    "QuarantineRow",
    "QuarantineStore",
    "ViolationLike",
    "jsonable",
    "quarantine_id",
]

log = get_logger(__name__)

_TABLE = "quarantine"


class ViolationLike(Protocol):
    """構造制約違反 1 件の形（``bridge.validate.ValidationFinding`` が満たす）。

    この層は ``bridge`` を import できないので（層の向き）、必要な属性だけを Protocol にする。
    """

    @property
    def source_shape(self) -> str:
        """違反した形状の IRI。"""
        ...

    @property
    def message(self) -> str:
        """形状のメッセージ。"""
        ...

    @property
    def focus_node(self) -> str:
        """違反の焦点ノード。"""
        ...

    @property
    def path(self) -> str | None:
        """違反したプロパティパス。"""
        ...


def jsonable(value: Any) -> Any:
    """jsonb に入る形にする（NUL 文字・NaN / Infinity は Postgres が拒否する）。

    隔離の対象は壊れた入力そのものなので、「壊れた入力が書き込みを壊す」ことを許さない
    ——書けないと、ワーカーが毎周期同じ行で落ちる。
    """
    if isinstance(value, str):
        return value.replace("\x00", "\\x00")
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {str(jsonable(k)): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return jsonable(str(value))


def quarantine_id(*, site: str, source: str, table: str, subject: str, reason: str) -> UUID:
    """隔離の識別子（同じ入力が同じ理由で落ちたら同じ値）。

    Args:
        site: 拠点識別子。
        source: 情報源 IRI。
        table: 業務システム側の表名（または検証した対象の種類）。
        subject: 主語（外部 ID・焦点ノード）。
        reason: 隔離の理由。
    """
    return uuid5(NAMESPACE_URL, f"quarantine|{site}|{source}|{table}|{subject}|{reason}")


@dataclass(frozen=True, slots=True)
class QuarantineRow:
    """隔離 1 件。

    Attributes:
        event_id: 識別子。
        topic: 入口（``ingest:<表>`` など）。
        subject: 主語（外部 ID・焦点ノード）。
        reason: 隔離の理由（人が読む）。
        payload: 生の入力（捨てない）。
        quarantined_at: 隔離した時刻。
        reviewed_at: 確認した時刻（未確認は ``None``）。
        resolution: 確認の結果（人が書く）。
    """

    event_id: UUID
    topic: str
    subject: str
    reason: str
    payload: dict[str, Any] = field(default_factory=dict)
    quarantined_at: datetime | None = None
    reviewed_at: datetime | None = None
    resolution: str | None = None


class QuarantineReviewCommand(BaseModel):
    """隔離した入力の確認（`/commands/review_quarantine`。M10-02、ADR-0035）。

    Attributes:
        decision_id: 冪等の鍵。
        quarantine_id: 対象の隔離（``quarantine`` クエリの ``eventId``）。
        resolution: 確認の結果（人が書く。例: 「業務側で修正済み」「対応不要」）。
            **空を許さない**——根拠の無い確認は監査で読めない。
        reviewed_by: 確認した主体の IRI。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(min_length=1)
    quarantine_id: UUID
    resolution: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)


class QuarantineStore:
    """隔離キューの読み書き。

    Attributes:
        site: 拠点識別子。
    """

    def __init__(self, connection: AsyncConnection, *, site: str) -> None:
        """Args:
        connection: Postgres 接続（async）。
        site: 拠点識別子。
        """
        self._connection = connection
        self.site = site

    async def add(
        self, *, source: str, table: str, subject: str, reason: str, payload: dict[str, Any]
    ) -> bool:
        """隔離に 1 件積む（同じ入力が同じ理由なら積まない）。

        Args:
            source: 情報源 IRI。
            table: 表名（または対象の種類）。
            subject: 主語。
            reason: 隔離の理由。
            payload: 生の入力。

        Returns:
            新しく積まれたか（既にあれば、確認済みでも偽。未確認の既存行は生の入力だけ更新する）。
        """
        event_id = quarantine_id(
            site=self.site, source=source, table=table, subject=subject, reason=reason
        )
        body = {"source": source, "table": table, **payload}
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO {_TABLE} (event_id, site, topic, subject, reason, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb) "
                # 未確認の行は最新の生の入力に更新する。確認済みは触らない
                "ON CONFLICT (event_id) DO UPDATE SET payload = EXCLUDED.payload "
                f"WHERE {_TABLE}.reviewed_at IS NULL RETURNING (xmax = 0)",
                (
                    event_id,
                    self.site,
                    f"ingest:{table}",
                    subject,
                    reason,
                    json.dumps(jsonable(body), ensure_ascii=False, sort_keys=True),
                ),
            )
            returned = await cursor.fetchone()
            inserted = returned is not None and bool(returned[0])
        log.info(
            "knowledge.quarantined",
            site=self.site,
            event_id=str(event_id),
            table=table,
            subject=subject,
            inserted=inserted,
        )
        return inserted

    async def add_violations(
        self,
        violations: Sequence[ViolationLike],
        *,
        source: str,
        table: str,
        payload: dict[str, Any],
    ) -> int:
        """SHACL の構造制約違反（``ValidationOutcome.violations``）を隔離に積む。

        整合制約（``sh:Warning``）は渡さない——登録して不一致の候補にする（設計書 §3.9）。

        Args:
            violations: 構造制約違反。
            source: 情報源 IRI。
            table: 検証した対象の種類。
            payload: 生の入力。

        Returns:
            新しく積んだ件数。
        """
        added = 0
        for finding in violations:
            reason = f"{finding.source_shape}: {finding.message}"
            if await self.add(
                source=source,
                table=table,
                subject=finding.focus_node,
                reason=reason,
                payload={**payload, "path": finding.path},
            ):
                added += 1
        return added

    async def recent(self, *, only_unreviewed: bool = True, limit: int = 50) -> list[QuarantineRow]:
        """新しい順に返す（読み出し用）。

        Args:
            only_unreviewed: 未確認だけを返すか。
            limit: 返す件数の上限。
        """
        where = "AND reviewed_at IS NULL" if only_unreviewed else ""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT event_id, topic, subject, reason, payload, quarantined_at, "
                f"reviewed_at, resolution FROM {_TABLE} WHERE site = %s {where} "
                "ORDER BY quarantined_at DESC, event_id LIMIT %s",
                (self.site, limit),
            )
            rows = await cursor.fetchall()
        return [
            QuarantineRow(
                event_id=row[0],
                topic=str(row[1]),
                subject=str(row[2]),
                reason=str(row[3]),
                payload=row[4] if isinstance(row[4], dict) else json.loads(row[4]),
                quarantined_at=ensure_utc(row[5]),
                reviewed_at=None if row[6] is None else ensure_utc(row[6]),
                resolution=None if row[7] is None else str(row[7]),
            )
            for row in rows
        ]

    async def mark_reviewed(self, event_id: UUID, *, resolution: str, at: datetime) -> bool:
        """確認済みにする（一度だけ。既に確認済みの行は変えない）。

        Args:
            event_id: 隔離の識別子。
            resolution: 確認の結果（人が書く）。
            at: 確認した時刻。

        Returns:
            更新したか（無い・確認済みなら偽）。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"UPDATE {_TABLE} SET reviewed_at = %s, resolution = %s "
                "WHERE event_id = %s AND site = %s AND reviewed_at IS NULL",
                (ensure_utc(at), resolution, event_id, self.site),
            )
            return cursor.rowcount == 1
