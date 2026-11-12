"""予測の保存と採点待ちの読み出し（M9-02、ADR-0032）。

予測は ``predicted`` のアサーション（平均）としてグラフに書かれるが、採点には区間と標本が要る。
ここに予測の全体（JSON）を持つ。**中身（``Forecast``）は ``reasoning`` の型なので、この層は
辞書として受け渡す**（層の向き: knowledge は reasoning を import しない）。

- 同じ ``(拠点, 実体, フルーエント, WM 版, 起点)`` は 1 件（再実行で二重に積まない）。
- 行は書いたら変えない。変わるのは ``scored_at`` だけ（一度だけ）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, LiteralString

from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from psycopg import AsyncConnection

__all__ = ["ForecastRecord", "ForecastStore"]

log = get_logger(__name__)

_TABLE = "forecasts"


@dataclass(frozen=True, slots=True)
class ForecastRecord:
    """保存した予測 1 件。

    Attributes:
        forecast_id: 識別子。
        entity: 実体 IRI。
        fluent: フルーエント型 IRI。
        horizon_origin: 予測の起点。
        horizon_end: 最も遠い先行時間の時刻。
        wm_version: WM 版 IRI。
        interval_level: 名目被覆。
        payload: 予測の全体（``Forecast.model_dump(mode="json")``）。
        scored_at: 採点した時刻（未採点なら ``None``）。
    """

    forecast_id: UUID
    entity: str
    fluent: str
    horizon_origin: datetime
    horizon_end: datetime
    wm_version: str
    interval_level: float
    payload: dict[str, Any]
    scored_at: datetime | None


class ForecastStore:
    """予測の表の読み書き。

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

    async def save(
        self,
        *,
        forecast_id: UUID,
        entity: str,
        fluent: str,
        horizon_origin: datetime,
        horizon_end: datetime,
        wm_version: str,
        interval_level: float,
        payload: dict[str, Any],
    ) -> bool:
        """予測を 1 件書く（同じ起点は書かない）。

        Args:
            forecast_id: 識別子。
            entity: 実体 IRI。
            fluent: フルーエント型 IRI。
            horizon_origin: 起点。
            horizon_end: 最も遠い先行時間の時刻。
            wm_version: WM 版 IRI。
            interval_level: 名目被覆。
            payload: 予測の全体。

        Returns:
            書いたら真、同じ起点が既にあれば偽。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO {_TABLE} (forecast_id, site, entity, fluent, horizon_origin, "
                "horizon_end, wm_version, interval_level, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (site, entity, fluent, wm_version, horizon_origin) DO NOTHING",
                (
                    forecast_id,
                    self.site,
                    entity,
                    fluent,
                    ensure_utc(horizon_origin),
                    ensure_utc(horizon_end),
                    wm_version,
                    interval_level,
                    json.dumps(payload),
                ),
            )
            written = cursor.rowcount == 1
        log.debug(
            "knowledge.forecast_saved",
            site=self.site,
            entity=entity,
            fluent=fluent,
            written=written,
        )
        return written

    async def due(self, *, before: datetime, limit: int = 500) -> list[ForecastRecord]:
        """採点の期限が来た未採点の予測を古い順に返す。

        Args:
            before: この時刻以前に地平が終わった予測。
            limit: 返す件数の上限。
        """
        return await self._select(
            "scored_at IS NULL AND horizon_end <= %s ORDER BY horizon_end LIMIT %s",
            (ensure_utc(before), limit),
        )

    async def recent(
        self, *, entity: str | None = None, fluent: str | None = None, limit: int = 50
    ) -> list[ForecastRecord]:
        """新しい起点の順に返す（読み出し用）。

        Args:
            entity: 実体 IRI で絞る。
            fluent: フルーエント型 IRI で絞る。
            limit: 返す件数の上限。
        """
        return await self._select(
            "(%s::text IS NULL OR entity = %s) AND (%s::text IS NULL OR fluent = %s) "
            "ORDER BY horizon_origin DESC LIMIT %s",
            (entity, entity, fluent, fluent, limit),
        )

    async def mark_scored(self, ids: Sequence[UUID], *, at: datetime) -> int:
        """採点済みにする（一度だけ。既に採点済みの行は変えない）。

        Args:
            ids: 予測の識別子。
            at: 採点時刻。

        Returns:
            更新した件数。
        """
        if not ids:
            return 0
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"UPDATE {_TABLE} SET scored_at = %s "
                "WHERE site = %s AND forecast_id = ANY(%s) AND scored_at IS NULL",
                (ensure_utc(at), self.site, list(ids)),
            )
            return cursor.rowcount

    async def _select(self, tail: LiteralString, params: tuple[Any, ...]) -> list[ForecastRecord]:
        columns: LiteralString = (
            "forecast_id, entity, fluent, horizon_origin, horizon_end, wm_version, "
            "interval_level, payload, scored_at"
        )
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"SELECT {columns} FROM {_TABLE} WHERE site = %s AND {tail}",
                (self.site, *params),
            )
            rows = await cursor.fetchall()
        return [
            ForecastRecord(
                forecast_id=row[0],
                entity=str(row[1]),
                fluent=str(row[2]),
                horizon_origin=ensure_utc(row[3]),
                horizon_end=ensure_utc(row[4]),
                wm_version=str(row[5]),
                interval_level=float(row[6]),
                payload=row[7] if isinstance(row[7], dict) else json.loads(row[7]),
                scored_at=None if row[8] is None else ensure_utc(row[8]),
            )
            for row in rows
        ]
