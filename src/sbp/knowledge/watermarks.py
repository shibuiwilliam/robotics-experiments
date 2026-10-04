"""接地ワーカーのウォーターマークの永続化（M8-03）。

「最後に処理した潜在の時刻」を実体ごとに持つ。M7 の実装ではプロセス内のメモリにあり、
再起動すると**取りこぼすか重複するか**のどちらかになっていた（どちらも静かに起こる）。

**キーに WM 版と階層が入る。** 違う潜在空間・違う階層のウォーターマークは意味が違う。
版を上げたときに古い値を流用すると、新しい版の潜在を「もう処理した」ことにしてしまう。

**書き込みは副作用の後に行う。** 進めてから送ると、送信に失敗したイベントが二度と昇格され
ない（潜在は残っているが読み直さない）。順序はワーカーの責務だが、この表の意味がそれを
前提にしていることをここに書いておく（所見 52）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from datetime import datetime

    from psycopg import AsyncConnection

__all__ = ["Watermark", "WatermarkStore"]

log = get_logger(__name__)

_TABLE = "grounding_watermarks"


@dataclass(frozen=True, slots=True)
class Watermark:
    """1 実体ぶんのウォーターマーク。

    Attributes:
        entity: 実体 IRI。
        processed_through: この時刻までの潜在を処理した（``LatentIndex.since`` は右開なので
            次はこの時刻より後を読む）。
    """

    entity: str
    processed_through: datetime


class WatermarkStore:
    """ウォーターマークの読み書き。

    Attributes:
        worker: ワーカー名（1 つの表を複数のワーカーが使うため）。
        wm_version: 対象の WM 版 IRI。
        level: 潜在の階層。
    """

    def __init__(
        self,
        connection: AsyncConnection,
        *,
        worker: str,
        wm_version: str,
        level: str,
    ) -> None:
        """Args:
        connection: Postgres 接続（async）。
        worker: ワーカー名。
        wm_version: 対象の WM 版 IRI。
        level: 潜在の階層（``fast`` / ``mid`` / ``slow``）。
        """
        self._connection = connection
        self.worker = worker
        self.wm_version = wm_version
        self.level = level

    async def load(self) -> dict[str, datetime]:
        """この (ワーカー, 版, 階層) のウォーターマークを全部読む。

        **起動時に 1 回読む形を想定している。** 実体ごとに毎ループ問い合わせると、実体数に
        比例してクエリが増える割に、値はワーカー自身が持っているものと同じである。

        Returns:
            実体 IRI → 処理済みの時刻。登録の無い実体は含まれない（「無い」を既定値で
            埋めない——埋めると、はじめて見る実体が「最新から始める」のか
            「最初から読む」のか呼び出し側が決められなくなる）。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"SELECT entity, processed_through FROM {_TABLE} "
                "WHERE worker = %s AND wm_version = %s AND level = %s",
                (self.worker, self.wm_version, self.level),
            )
            rows = await cursor.fetchall()
        found = {str(row[0]): ensure_utc(row[1]) for row in rows}
        log.info(
            "knowledge.watermarks_loaded",
            worker=self.worker,
            wm_version=self.wm_version,
            level=self.level,
            entities=len(found),
        )
        return found

    async def save(self, entity: str, processed_through: datetime) -> None:
        """1 実体ぶんを書く（UPSERT）。

        **後退させない。** ``processed_through`` が既存の値より古い更新は無視する
        （``WHERE ... < EXCLUDED``）。後退を許すと、遅れて走ったループが進んだ位置を
        巻き戻し、同じ潜在が二度処理される。

        Args:
            entity: 実体 IRI。
            processed_through: この時刻までを処理した。
        """
        moment = ensure_utc(processed_through)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO {_TABLE} "
                "(worker, entity, wm_version, level, processed_through) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (worker, entity, wm_version, level) DO UPDATE "
                "SET processed_through = EXCLUDED.processed_through, updated_at = now() "
                f"WHERE {_TABLE}.processed_through < EXCLUDED.processed_through",
                (self.worker, entity, self.wm_version, self.level, moment),
            )
        log.debug(
            "knowledge.watermark_saved",
            worker=self.worker,
            entity=entity,
            processed_through=moment.isoformat(),
        )
