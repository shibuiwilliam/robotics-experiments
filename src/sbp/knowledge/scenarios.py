"""What-if のシナリオの索引（M10-03、ADR-0036）。

結果の ``hypothetical`` アサーションはシナリオ用の名前付きグラフにある（主信念に入れない。
不変条件 3）。この表は「どのシナリオがあるか」と、基準・介入ありの確率の曲線を持つ読み出し用の索引。
**中身（``Scenario``）は ``reasoning`` の型なので、辞書のまま受け渡す**（層の向き）。
行は書いたら変えない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from psycopg import AsyncConnection

__all__ = ["ScenarioRecord", "ScenarioStore"]

log = get_logger(__name__)

_TABLE = "scenarios"


@dataclass(frozen=True, slots=True)
class ScenarioRecord:
    """保存したシナリオ 1 件。

    Attributes:
        scenario_id: 識別子（シナリオグラフの名前に使う）。
        entity: 実体 IRI。
        fluent: フルーエント型 IRI。
        wm_version: WM 版 IRI。
        origin: 起点の時刻。
        action_type: 介入したアクション型 IRI。
        payload: 曲線・介入・説明（JSON）。
    """

    scenario_id: UUID
    entity: str
    fluent: str
    wm_version: str
    origin: datetime
    action_type: str
    payload: dict[str, Any]


class ScenarioStore:
    """シナリオの索引の読み書き。

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
        scenario_id: UUID,
        entity: str,
        fluent: str,
        wm_version: str,
        origin: datetime,
        action_type: str,
        payload: dict[str, Any],
    ) -> bool:
        """シナリオを 1 件書く（同じ識別子は書かない）。

        Args:
            scenario_id: 識別子（決定的に決める）。
            entity: 実体 IRI。
            fluent: フルーエント型 IRI。
            wm_version: WM 版 IRI。
            origin: 起点の時刻。
            action_type: 介入したアクション型 IRI。
            payload: 曲線・介入・説明。

        Returns:
            書いたら真、同じ識別子が既にあれば偽。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO {_TABLE} (scenario_id, site, entity, fluent, wm_version, origin, "
                "action_type, payload) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (scenario_id) DO NOTHING",
                (
                    scenario_id,
                    self.site,
                    entity,
                    fluent,
                    wm_version,
                    ensure_utc(origin),
                    action_type,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            written = cursor.rowcount == 1
        log.info(
            "knowledge.scenario_saved", site=self.site, scenario=str(scenario_id), written=written
        )
        return written

    async def recent(
        self, *, entity: str | None = None, fluent: str | None = None, limit: int = 50
    ) -> list[ScenarioRecord]:
        """新しい起点の順に返す（読み出し用）。

        Args:
            entity: 実体 IRI で絞る。
            fluent: フルーエント型 IRI で絞る。
            limit: 返す件数の上限。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT scenario_id, entity, fluent, wm_version, origin, action_type, payload "
                f"FROM {_TABLE} WHERE site = %s "
                "AND (%s::text IS NULL OR entity = %s) AND (%s::text IS NULL OR fluent = %s) "
                "ORDER BY origin DESC, scenario_id LIMIT %s",
                (self.site, entity, entity, fluent, fluent, limit),
            )
            rows = await cursor.fetchall()
        return [
            ScenarioRecord(
                scenario_id=row[0],
                entity=str(row[1]),
                fluent=str(row[2]),
                wm_version=str(row[3]),
                origin=ensure_utc(row[4]),
                action_type=str(row[5]),
                payload=row[6] if isinstance(row[6], dict) else json.loads(row[6]),
            )
            for row in rows
        ]
