"""取込ワーカーのカーソルの永続化（M8-25-5、ADR-0016）。

業務システムの表を「前回どこまで読んだか」（``updated_at`` の最大）を ``(拠点, 情報源, 表)`` ごとに
持つ。形は接地ワーカーのウォーターマーク（``WatermarkStore``）と同じで、**後退させない**。

**書き込みは送信の後に行う**（所見 52）。進めてから送ると、送信に失敗した行を二度と読まない。
順序はワーカーの責務だが、この表の意味がそれを前提にしていることをここに書いておく。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from datetime import datetime

    from psycopg import AsyncConnection

__all__ = ["IngestCursorStore"]

log = get_logger(__name__)

_TABLE = "ingest_cursors"


class IngestCursorStore:
    """取込カーソルの読み書き。

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

    async def load(self) -> dict[tuple[str, str], datetime]:
        """この拠点のカーソルを全部読む（起動時に 1 回）。

        Returns:
            ``(情報源 IRI, 表名)`` → 読み終えた ``updated_at``。登録の無い表は含まれない
            （「無い」を既定値で埋めない——埋めると、はじめての表を全行から読むのか最新から読むのか
            呼び出し側が決められなくなる）。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"SELECT source, table_name, pulled_through FROM {_TABLE} WHERE site = %s",
                (self.site,),
            )
            rows = await cursor.fetchall()
        found = {(str(row[0]), str(row[1])): ensure_utc(row[2]) for row in rows}
        log.info("knowledge.ingest_cursors_loaded", site=self.site, tables=len(found))
        return found

    async def save(self, source: str, table: str, pulled_through: datetime) -> None:
        """1 表ぶんを書く（UPSERT）。

        **後退させない**（``WHERE ... < EXCLUDED``）。後退を許すと、遅れて走ったループが進んだ
        位置を巻き戻し、同じ行を読み直す。

        Args:
            source: 情報源 IRI。
            table: 業務システム側の表名。
            pulled_through: この ``updated_at`` までを送信し終えた。
        """
        moment = ensure_utc(pulled_through)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO {_TABLE} (site, source, table_name, pulled_through) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (site, source, table_name) DO UPDATE "
                "SET pulled_through = EXCLUDED.pulled_through, updated_at = now() "
                f"WHERE {_TABLE}.pulled_through < EXCLUDED.pulled_through",
                (self.site, source, table, moment),
            )
        log.debug(
            "knowledge.ingest_cursor_saved",
            site=self.site,
            source=source,
            table=table,
            pulled_through=moment.isoformat(),
        )
