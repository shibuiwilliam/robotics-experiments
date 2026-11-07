"""業務記録の存在の投影（M8-27-2、ADR-0017）。

取込（ingest-worker）が送った記録の「存在」だけを追記専用で持つ。**記録の中身は持たない**
（業務システムが system of record のまま）。期待の記号側の検証が
「この実体にこの型の記録が、期待の作成後にあるか」を引く。

**「無い」を断定しない。** 取込が遅れているだけでも行は無い。呼び出し側（期待の検証）は期限切れの
ものだけを引くので、期限までの取込の遅れは猶予として吸収される。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from psycopg import AsyncConnection

    from sbp.core.records import Record

__all__ = ["RecordIndex"]

log = get_logger(__name__)

_TABLE = "business_records"


class RecordIndex:
    """業務記録の存在の読み書き。

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

    async def add(self, records: Sequence[Record]) -> int:
        """記録の存在を追記する（冪等）。対象が未解決の記録は引けないので入れない。

        Args:
            records: 送信し終えた記録。

        Returns:
            新しく入った行数。
        """
        rows = [
            (self.site, record.is_about, record.record_type, record.external_id, record.recorded_at)
            for record in records
            if record.is_about is not None
        ]
        if not rows:
            return 0
        added = 0
        async with self._connection.cursor() as cursor:
            for row in rows:
                await cursor.execute(
                    f"INSERT INTO {_TABLE} (site, entity, record_type, external_id, recorded_at) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    row,
                )
                added += max(cursor.rowcount, 0)
        log.debug("knowledge.business_records_added", site=self.site, added=added)
        return added

    async def present_types(self, entity: str, *, since: datetime) -> frozenset[str]:
        """``since`` 以降に記録のある型の集合を引く。

        Args:
            entity: 対象実体の IRI。
            since: この時刻以降（含む）に計上された記録を数える。

        Returns:
            記録型 IRI の集合。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"SELECT DISTINCT record_type FROM {_TABLE} "
                "WHERE site = %s AND entity = %s AND recorded_at >= %s",
                (self.site, entity, ensure_utc(since)),
            )
            rows = await cursor.fetchall()
        return frozenset(str(row[0]) for row in rows)
