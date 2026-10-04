"""人の判断の作業キュー（M8-08、ADR-0012）。

UI が `/commands/*` に投げた判断（不一致の解決・概念提案の採否）を受け、
``learning-worker`` が引いて適用する。

**受け付けと適用を分ける理由。** 書き戻しは業務システムへの外部呼び出しで、数秒〜数十秒かかり
相手の障害で失敗する。API のハンドラで同期的に適用すると、**人が書き戻しの完了を待ち、
失敗したら判断そのものが失われる**（UI が再入力を求め、同じ判断が二重に入りうる）。

**行を消さない。** 適用に失敗した判断も ``failed_at`` と ``error`` を残す——消すと
「人が判断したのに何も起きなかった」状態が説明できない。

**``ObservationRequest`` と同じく、この層は上位層の型を知らない**（層依存の向き）。
:class:`DecisionRow` は ``payload`` を ``dict`` のまま持ち、``Resolution`` などへの変換は
ワーカーが行う。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from datetime import datetime

    from psycopg import AsyncConnection

__all__ = ["DecisionKind", "DecisionQueue", "DecisionRow"]

log = get_logger(__name__)

_TABLE = "learning_decisions"


class DecisionKind(StrEnum):
    """判断の種類（表の ``kind`` 列の CHECK と一致させる）。"""

    RESOLUTION = "resolution"
    """不一致の解決（物理が正しい / 記録が正しい / 両方誤り / 判定不能）。"""
    CURATION = "curation"
    """概念提案の採否。"""


@dataclass(frozen=True, slots=True)
class DecisionRow:
    """キューの 1 行。

    Attributes:
        decision_id: 判断の識別子（冪等の鍵。同じ判断を二度適用しない）。
        kind: 判断の種類。
        subject: 対象（不一致 ID または提案 ID）。
        decided_by: 判断した主体の IRI。
        payload: 判断の内容（種類ごとに形が違うので ``dict`` のまま持つ）。
        received_at: 受け付けた時刻。
        attempts: 適用を試みた回数。
    """

    decision_id: str
    kind: DecisionKind
    subject: str
    decided_by: str
    payload: dict[str, Any] = field(default_factory=dict)
    received_at: datetime | None = None
    attempts: int = 0


class DecisionQueue:
    """判断の読み書き。

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

    async def enqueue(self, row: DecisionRow) -> bool:
        """判断を積む。

        **同じ ``decision_id`` を二度積まない。** UI の再送（ネットワークの再試行、人の
        二度押し）で同じ判断が 2 回適用されると、書き戻しが 2 回走る。`WritebackCommand` は
        冪等な ``command_id`` を持つが、**ここで止めるほうが早い**。

        Args:
            row: 積む判断。

        Returns:
            新しく積まれたか（既にあれば偽）。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO {_TABLE} "
                "(decision_id, site, kind, subject, decided_by, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (decision_id) DO NOTHING",
                (
                    row.decision_id,
                    self.site,
                    row.kind.value,
                    row.subject,
                    row.decided_by,
                    json.dumps(row.payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            inserted = cursor.rowcount == 1
        log.info(
            "knowledge.decision_enqueued",
            site=self.site,
            decision_id=row.decision_id,
            kind=row.kind.value,
            subject=row.subject,
            inserted=inserted,
        )
        return inserted

    async def pending(self, *, limit: int = 50) -> list[DecisionRow]:
        """未適用の判断を古い順に返す。

        **古い順に適用する。** 人が順に判断したなら、その順で適用するのが期待に合う
        （後の判断が前の判断を前提にしうる）。

        **失敗した判断は返さない。** 無限に再試行しない（ADR-0012 の未決: 打ち切ったものを
        人が再投入する経路は未実装）。

        Args:
            limit: 返す件数の上限。

        Returns:
            判断の列（受け付け順）。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT decision_id, kind, subject, decided_by, payload, received_at, attempts "
                f"FROM {_TABLE} WHERE site = %s AND applied_at IS NULL AND failed_at IS NULL "
                "ORDER BY received_at LIMIT %s",
                (self.site, limit),
            )
            rows = await cursor.fetchall()
        return [
            DecisionRow(
                decision_id=str(row[0]),
                kind=DecisionKind(str(row[1])),
                subject=str(row[2]),
                decided_by=str(row[3]),
                payload=dict(row[4] or {}),
                received_at=ensure_utc(row[5]),
                attempts=int(row[6]),
            )
            for row in rows
        ]

    async def mark_applied(
        self, decision_id: str, *, at: datetime, artifacts: dict[str, Any] | None = None
    ) -> None:
        """適用できたことを記録する。

        Args:
            decision_id: 判断の識別子。
            at: 適用時刻。
            artifacts: 何を適用したかの要約（UI が「この判断で何が起きたか」を読む）。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"UPDATE {_TABLE} SET applied_at = %s, attempts = attempts + 1, "
                "artifacts = %s::jsonb WHERE decision_id = %s AND site = %s",
                (
                    ensure_utc(at),
                    json.dumps(artifacts or {}, ensure_ascii=False, sort_keys=True),
                    decision_id,
                    self.site,
                ),
            )
        log.info(
            "knowledge.decision_applied",
            site=self.site,
            decision_id=decision_id,
            artifacts=artifacts or {},
        )

    async def mark_failed(self, decision_id: str, *, at: datetime, error: str) -> None:
        """適用に失敗したことを記録する（行は消さない）。

        Args:
            decision_id: 判断の識別子。
            at: 失敗時刻。
            error: 失敗の理由（人が読む）。**切り詰めない**——原因が分からないと再投入の
                判断ができない。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"UPDATE {_TABLE} SET failed_at = %s, attempts = attempts + 1, error = %s "
                "WHERE decision_id = %s AND site = %s",
                (ensure_utc(at), error, decision_id, self.site),
            )
        log.error(
            "knowledge.decision_failed",
            site=self.site,
            decision_id=decision_id,
            error=error,
            detail="人の判断が適用できなかった。行は残るので人が再投入できる",
        )
