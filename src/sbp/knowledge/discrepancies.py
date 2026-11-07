"""不一致の作業項目の読み書き（M8-12、ADR-0013）。

不一致は設計書 §8.2 の「製品」であり、状態機械を持つ作業項目である。この層はその永続化を担う。

**純粋な投影ではない。** 起票イベント（ADR-0010）は**通知**であって不一致そのものではない
——``EventEnvelope`` は様相と信念を必須に持つ形で、根拠アサーション・状態履歴・深刻度の内訳・
解決・学習信号を入れる場所が無い（``docs/PROGRESS.md`` 所見 62）。したがって照合ワーカーが
この表に直接書く。規律の例外なので、理由を ADR-0013 と表のコメントに残してある。

**``Discrepancy`` の型を知らない。** その型は ``reasoning`` にあり、``knowledge`` は下位層なので
import できない（層依存の向き）。この層は :class:`DiscrepancyRecord`（問い合わせ用の列 ＋
本体の dict）を扱い、``Discrepancy`` への検証は呼び出し側が行う——``ObservationRow`` /
``DecisionRow`` と同じ形である。

**状態の後退を拒否する。** 書き込みは ``Discrepancy.transition()`` を通った結果だけが来る前提
だが、複数の経路（照合ワーカーと learning-worker）が同じ行を触るので、片方が古い状態で
上書きする事故が起こりうる。終端状態（``Learned`` / ``Suppressed``）から非終端へ戻す更新は
表の側でも弾く。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from psycopg import sql

from sbp.core.errors import KnowledgeError
from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg import AsyncConnection

__all__ = ["DiscrepancyRecord", "DiscrepancyStore"]

log = get_logger(__name__)

_TABLE = "discrepancies"

_TERMINAL_STATUSES = frozenset({"Learned", "Suppressed"})
"""終端状態（``DiscrepancyStatus.is_terminal`` と一致させる）。

**``reasoning`` の列挙を import できない**ので値を写している。食い違うと後退の拒否が効か
なくなるため、単体テストで両者の一致を検査する。
"""


@dataclass(frozen=True, slots=True)
class DiscrepancyRecord:
    """表の 1 行（問い合わせ用の列 ＋ 本体）。

    Attributes:
        discrepancy_id: 識別子。
        kind: 五類型（1〜5）。
        status: ライフサイクル状態の値（``Open`` など）。
        subjects: 対象の実体 IRI。
        status_rank: ライフサイクル上の位置（``DiscrepancyStatus.rank``）。**後退の検出に
            使う**——終端だけを守ると、``Resolved`` の行を古い ``Open`` で上書きできてしまう
            （所見 80）。
        severity_score: 深刻度の総合スコア（並べ替えに使う）。
        detected_at: 検出時刻。
        payload: 本体（``Discrepancy.model_dump(mode="json")``）。
    """

    discrepancy_id: UUID
    kind: int
    status: str
    status_rank: int
    subjects: tuple[str, ...]
    severity_score: float
    detected_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """終端状態か（キューから外れるか）。"""
        return self.status in _TERMINAL_STATUSES


class DiscrepancyStore:
    """不一致の作業項目の読み書き。

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

    async def upsert(self, record: DiscrepancyRecord) -> None:
        """不一致を書く（同じ識別子は更新）。

        **ライフサイクルを後退させられない。** 複数の経路（照合ワーカーと learning-worker）が
        同じ行を触るので、片方が古い状態で上書きする事故が起こりうる。``status_rank`` が
        下がる更新を表の側でも弾く（同じ位置なら内容の更新として通す）。

        Args:
            record: 書く不一致。

        Raises:
            KnowledgeError: 状態を後退させようとした場合。**黙って無視しない**——呼び出し側が
                「書けた」と思って進むと、解決済みの不一致がキューに戻ったように見える。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO {_TABLE} "
                "(discrepancy_id, site, kind, status, status_rank, subjects, severity_score, "
                "detected_at, terminal, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (discrepancy_id) DO UPDATE SET "
                "status = EXCLUDED.status, status_rank = EXCLUDED.status_rank, "
                "severity_score = EXCLUDED.severity_score, subjects = EXCLUDED.subjects, "
                "terminal = EXCLUDED.terminal, payload = EXCLUDED.payload, updated_at = now() "
                f"WHERE {_TABLE}.status_rank <= EXCLUDED.status_rank",
                (
                    str(record.discrepancy_id),
                    self.site,
                    record.kind,
                    record.status,
                    record.status_rank,
                    list(record.subjects),
                    record.severity_score,
                    ensure_utc(record.detected_at),
                    record.is_terminal,
                    json.dumps(record.payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            written = cursor.rowcount
        if written == 0:
            raise KnowledgeError(
                f"discrepancy {record.discrepancy_id} is further along its lifecycle than "
                f"{record.status!r} (rank {record.status_rank}); refusing to move it backwards. "
                "A resolved discrepancy must not reappear in the queue (ADR-0013)."
            )
        log.info(
            "knowledge.discrepancy_written",
            site=self.site,
            correlation_id=str(record.discrepancy_id),
            kind=record.kind,
            status=record.status,
            severity=record.severity_score,
        )

    async def get(self, discrepancy_id: UUID | str) -> DiscrepancyRecord | None:
        """識別子で 1 件引く。

        Args:
            discrepancy_id: 識別子。

        Returns:
            行、無ければ ``None``。**例外にしない**——呼び出し側（learning-worker）は
            「本体が無いので最小の不一致で代替する」判断ができる必要がある。

        Raises:
            KnowledgeError: 識別子が UUID でない場合。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT discrepancy_id, kind, status, status_rank, subjects, severity_score, "
                f"detected_at, payload FROM {_TABLE} WHERE discrepancy_id = %s AND site = %s",
                (str(_as_uuid(discrepancy_id)), self.site),
            )
            row = await cursor.fetchone()
        return None if row is None else _to_record(row)

    async def queue(
        self, *, limit: int = 50, kind: int | None = None, include_terminal: bool = False
    ) -> list[DiscrepancyRecord]:
        """未解決の不一致を深刻度の高い順に返す（UI の不一致キュー）。

        **終端状態を既定で除く。** 解決済みを毎回返すと、キューの上位が履歴で埋まる。
        履歴を見るときだけ ``include_terminal`` を立てる。

        Args:
            limit: 返す件数の上限。
            kind: 五類型で絞る（``None`` なら絞らない）。
            include_terminal: 終端状態（``Learned`` / ``Suppressed``）も含めるか。

        Returns:
            行の列（深刻度降順、同値なら新しい順）。
        """
        # 条件は定数の組み立てで、値はすべてプレースホルダに入る（文字列連結で値を埋めない）。
        # `sql.SQL` で組むのは、psycopg の型が生の文字列の組み立てを受けないため
        conditions = [sql.SQL("site = %s")]
        params: list[object] = [self.site]
        if not include_terminal:
            conditions.append(sql.SQL("NOT terminal"))
        if kind is not None:
            conditions.append(sql.SQL("kind = %s"))
            params.append(kind)
        params.append(limit)
        statement = sql.SQL(
            "SELECT discrepancy_id, kind, status, status_rank, subjects, severity_score, "
            "detected_at, payload FROM {table} WHERE {where} "
            "ORDER BY severity_score DESC, detected_at DESC LIMIT %s"
        ).format(table=sql.Identifier(_TABLE), where=sql.SQL(" AND ").join(conditions))
        async with self._connection.cursor() as cursor:
            await cursor.execute(statement, tuple(params))
            rows = await cursor.fetchall()
        found = [_to_record(row) for row in rows]
        log.debug(
            "knowledge.discrepancy_queue_read",
            site=self.site,
            found=len(found),
            kind=kind,
            include_terminal=include_terminal,
        )
        return found

    async def resolved(self, *, limit: int = 1000) -> list[DiscrepancyRecord]:
        """人が解決した不一致（``Resolved`` / ``Learned``）を新しい順に返す（M8-09、ADR-0019）。

        概念発見の入口 (c)「反復する不一致」の入力である。**``Suppressed`` を含めない**——
        抑制は信頼性モデルで説明できた系統誤差で、人が語彙の不足を埋めた記録ではない。

        Args:
            limit: 返す件数の上限（発見は直近の傾向を見る）。

        Returns:
            行の列（検出の新しい順）。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT discrepancy_id, kind, status, status_rank, subjects, severity_score, "
                f"detected_at, payload FROM {_TABLE} "
                "WHERE site = %s AND status IN ('Resolved', 'Learned') "
                "ORDER BY detected_at DESC LIMIT %s",
                (self.site, limit),
            )
            rows = await cursor.fetchall()
        return [_to_record(row) for row in rows]

    async def count_by_status(self) -> dict[str, int]:
        """状態ごとの件数（運用の棚卸しと指標に使う）。

        Returns:
            状態 → 件数。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"SELECT status, count(*) FROM {_TABLE} WHERE site = %s GROUP BY status",
                (self.site,),
            )
            rows = await cursor.fetchall()
        return {str(row[0]): int(row[1]) for row in rows}


def _as_uuid(value: UUID | str) -> UUID:
    """識別子を UUID にする。

    Args:
        value: UUID または文字列。

    Raises:
        KnowledgeError: UUID として読めない場合。
    """
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value)
    except ValueError as exc:
        raise KnowledgeError(f"discrepancy id {value!r} is not a UUID") from exc


def _to_record(row: Sequence[Any]) -> DiscrepancyRecord:
    """SELECT の行を :class:`DiscrepancyRecord` にする。

    Args:
        row: ``(id, kind, status, status_rank, subjects, severity_score, detected_at,
            payload)``。
    """
    return DiscrepancyRecord(
        discrepancy_id=_as_uuid(str(row[0])),
        kind=int(row[1]),
        status=str(row[2]),
        status_rank=int(row[3]),
        subjects=tuple(str(subject) for subject in (row[4] or ())),
        severity_score=float(row[5]),
        detected_at=ensure_utc(row[6]),
        payload=dict(row[7] or {}),
    )
