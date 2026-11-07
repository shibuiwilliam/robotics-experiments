"""期待の作業項目の読み書き（M8-18）。

不一致（:mod:`sbp.knowledge.discrepancies`、ADR-0013）と同じ形である——期待も状態機械を持つ
作業項目で、``EventEnvelope`` に全体が入らない（``expected_effects`` / ``verification`` /
``history`` を入れる場所が無い。``docs/PROGRESS.md`` 所見 62）。

**純粋な投影ではない。** 生成した側（アクション実行）が直接書く。理由は ADR-0013 と同じで、
イベントから期待を再構成できない。

**``Expectation`` の型を知らない**（層依存の向き）。:class:`ExpectationRecord`（問い合わせ用の
列 ＋ 本体の dict）を扱い、検証は呼び出し側が行う。

**ライフサイクルを後退させられない。** ``status_rank`` が下がる更新を表の側でも弾く
（所見 80 と同じ対処）。
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

__all__ = ["ExpectationRecord", "ExpectationStore"]

log = get_logger(__name__)

_TABLE = "expectations"

_TERMINAL_STATUSES = frozenset({"Learned"})
"""終端状態（``ExpectationStatus.is_terminal`` と一致させる）。

**``reasoning`` の列挙を import できない**ので値を写している。食い違うと後退の拒否が効か
なくなるため、単体テストで両者の一致を検査する。
"""


@dataclass(frozen=True, slots=True)
class ExpectationRecord:
    """表の 1 行（問い合わせ用の列 ＋ 本体）。

    Attributes:
        expectation_id: 識別子。
        status: ライフサイクル状態の値（``Created`` など）。
        status_rank: ライフサイクル上の位置（``ExpectationStatus.rank``）。
        action_type: アクション型 IRI。
        target: 対象実体の IRI。
        created_at: 生成時刻。
        deadline: 期限（``expected_effects`` の最も遅い効果の期限。効果が無ければ ``None``）。
        payload: 本体（``Expectation.model_dump(mode="json")``）。
    """

    expectation_id: UUID
    status: str
    status_rank: int
    action_type: str
    target: str
    created_at: datetime
    deadline: datetime | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """終端状態か（ボードから外れるか）。"""
        return self.status in _TERMINAL_STATUSES


class ExpectationStore:
    """期待の作業項目の読み書き。

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

    async def upsert(self, record: ExpectationRecord) -> None:
        """期待を書く（同じ識別子は更新）。

        Args:
            record: 書く期待。

        Raises:
            KnowledgeError: 状態を後退させようとした場合。**黙って無視しない**——呼び出し側が
                「書けた」と思って進むと、検証済みの期待がボードに戻ったように見える。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO {_TABLE} "
                "(expectation_id, site, status, status_rank, action_type, target, created_at, "
                "deadline, terminal, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (expectation_id) DO UPDATE SET "
                "status = EXCLUDED.status, status_rank = EXCLUDED.status_rank, "
                "deadline = EXCLUDED.deadline, terminal = EXCLUDED.terminal, "
                "payload = EXCLUDED.payload, updated_at = now() "
                f"WHERE {_TABLE}.status_rank <= EXCLUDED.status_rank",
                (
                    str(record.expectation_id),
                    self.site,
                    record.status,
                    record.status_rank,
                    record.action_type,
                    record.target,
                    ensure_utc(record.created_at),
                    None if record.deadline is None else ensure_utc(record.deadline),
                    record.is_terminal,
                    json.dumps(record.payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            written = cursor.rowcount
        if written == 0:
            raise KnowledgeError(
                f"expectation {record.expectation_id} is further along its lifecycle than "
                f"{record.status!r} (rank {record.status_rank}); refusing to move it backwards. "
                "A verified expectation must not reappear on the board (M8-18)."
            )
        log.info(
            "knowledge.expectation_written",
            site=self.site,
            correlation_id=str(record.expectation_id),
            status=record.status,
            target=record.target,
        )

    async def get(self, expectation_id: UUID | str) -> ExpectationRecord | None:
        """識別子で 1 件引く。

        Args:
            expectation_id: 識別子。

        Returns:
            行、無ければ ``None``（例外にしない）。

        Raises:
            KnowledgeError: 識別子が UUID でない場合。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT expectation_id, status, status_rank, action_type, target, created_at, "
                f"deadline, payload FROM {_TABLE} WHERE expectation_id = %s AND site = %s",
                (str(_as_uuid(expectation_id)), self.site),
            )
            row = await cursor.fetchone()
        return None if row is None else _to_record(row)

    async def board(
        self, *, limit: int = 50, target: str | None = None, include_terminal: bool = False
    ) -> list[ExpectationRecord]:
        """未決着の期待を期限の近い順に返す（UI の期待ボード）。

        **期限が無いものは後ろに置く**（``NULLS LAST``）。``Created`` は期待効果をまだ持たない
        ので期限が無い——「期限切れが近い」判断の対象にならない。

        Args:
            limit: 返す件数の上限。
            target: 対象実体で絞る（``None`` なら絞らない）。
            include_terminal: 終端状態（``Learned``）も含めるか。

        Returns:
            行の列（期限の昇順、同値なら新しい順）。
        """
        conditions = [sql.SQL("site = %s")]
        params: list[object] = [self.site]
        if not include_terminal:
            conditions.append(sql.SQL("NOT terminal"))
        if target is not None:
            conditions.append(sql.SQL("target = %s"))
            params.append(target)
        params.append(limit)
        statement = sql.SQL(
            "SELECT expectation_id, status, status_rank, action_type, target, created_at, "
            "deadline, payload FROM {table} WHERE {where} "
            "ORDER BY deadline ASC NULLS LAST, created_at DESC LIMIT %s"
        ).format(table=sql.Identifier(_TABLE), where=sql.SQL(" AND ").join(conditions))
        async with self._connection.cursor() as cursor:
            await cursor.execute(statement, tuple(params))
            rows = await cursor.fetchall()
        found = [_to_record(row) for row in rows]
        log.debug(
            "knowledge.expectation_board_read",
            site=self.site,
            found=len(found),
            target=target,
            include_terminal=include_terminal,
        )
        return found

    async def overdue_active(self, *, at: datetime, limit: int = 50) -> list[ExpectationRecord]:
        """期限を過ぎた ``Active`` の期待を、期限の古い順に返す（M8-27、ADR-0017）。

        **検証してよいのは期限切れだけ**（早く検証すると「まだ判定できない」が「判定不能」に
        なる。所見 118）。期限の列は ``Expectation.deadline`` そのもの（最も遅い効果）。

        Args:
            at: この時刻より前に期限が来たものを返す（期限ちょうどは含めない。右開）。
            limit: 返す件数の上限。

        Returns:
            行の列（期限の昇順）。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT expectation_id, status, status_rank, action_type, target, created_at, "
                f"deadline, payload FROM {_TABLE} "
                "WHERE site = %s AND status = 'Active' AND deadline < %s "
                "ORDER BY deadline ASC LIMIT %s",
                (self.site, ensure_utc(at), limit),
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
        raise KnowledgeError(f"expectation id {value!r} is not a UUID") from exc


def _to_record(row: Sequence[Any]) -> ExpectationRecord:
    """SELECT の行を :class:`ExpectationRecord` にする。

    Args:
        row: ``(id, status, status_rank, action_type, target, created_at, deadline, payload)``。
    """
    return ExpectationRecord(
        expectation_id=_as_uuid(str(row[0])),
        status=str(row[1]),
        status_rank=int(row[2]),
        action_type=str(row[3]),
        target=str(row[4]),
        created_at=ensure_utc(row[5]),
        deadline=None if row[6] is None else ensure_utc(row[6]),
        payload=dict(row[7] or {}),
    )
