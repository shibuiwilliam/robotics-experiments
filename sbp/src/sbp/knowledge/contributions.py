"""信念の三源の寄与の投影（M8-17-2、ADR-0020）。

信念更新が出す三源の寄与（観測・記録・計画。対数尤度比の絶対値、設計書 §8.1）を、
``assertion_id`` をキーに追記専用で持つ。**アサーション本体（Oxigraph）には触れない**。証拠の
三源並置（設計書 §8.2、§10.3）がこれを時系列で読む。

**寄与は生の大きさである**（不変条件 7: ``belief`` ではない）。表示で割合にするのは読む側の仕事。
結合（``_couple``）が書く信念は寄与を持たないので、この表に行が無い——行が無いことは
「寄与は記録されていない」であって「寄与が 0」ではない。読む側はこの 2 つを区別する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from datetime import datetime

    from psycopg import AsyncConnection

__all__ = ["SERIES_LIMIT", "ContributionRecord", "ContributionStore"]

log = get_logger(__name__)

_TABLE = "belief_contributions"

SERIES_LIMIT = 1000
"""``series`` が既定で返す件数の上限。API は超えたことを呼び出し側に伝える（黙って切らない）。"""


@dataclass(frozen=True, slots=True)
class ContributionRecord:
    """信念 1 件の三源の寄与。

    Attributes:
        assertion_id: 対応するアサーションの識別子。
        entity: 対象実体の IRI。
        fluent: フルーエント型 IRI（アサーションの述語）。
        at: 信念の時刻。
        prior: 事前確率。
        posterior: 事後確率。
        observed: 物理観測（プローブ）の寄与（対数尤度比の絶対値）。
        record: 業務記録の寄与。
        plan: 計画の寄与。
        source_event_id: 起因したイベントの識別子。
        excluded_records: 監査隔離で除外した記録の数（不変条件 6 が働いた証拠）。
    """

    assertion_id: UUID
    entity: str
    fluent: str
    at: datetime
    prior: float
    posterior: float
    observed: float
    record: float
    plan: float
    source_event_id: str
    excluded_records: int = 0

    @property
    def dominant(self) -> str | None:
        """最も事後を動かした源（``observed`` / ``record`` / ``plan``）。

        全部 0 のとき、**最大が同点のときも** ``None``（同点で先頭の源に印を付けると、
        観測に偏った見え方になる）。
        """
        ranked = sorted(
            (("observed", self.observed), ("record", self.record), ("plan", self.plan)),
            key=lambda pair: pair[1],
            reverse=True,
        )
        top, runner_up = ranked[0], ranked[1]
        if top[1] <= 0.0 or top[1] == runner_up[1]:
            return None
        return top[0]


class ContributionStore:
    """寄与の読み書き。

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

    async def write(self, record: ContributionRecord) -> None:
        """寄与を 1 件追記する（同じ ``assertion_id`` は何もしない）。

        Args:
            record: 寄与。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO {_TABLE} (assertion_id, site, entity, fluent, at, prior, posterior, "
                "observed, record, plan, source_event_id, excluded_records) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (assertion_id) DO NOTHING",
                (
                    str(record.assertion_id),
                    self.site,
                    record.entity,
                    record.fluent,
                    ensure_utc(record.at),
                    record.prior,
                    record.posterior,
                    record.observed,
                    record.record,
                    record.plan,
                    record.source_event_id,
                    record.excluded_records,
                ),
            )

    async def series(
        self,
        *,
        entity: str,
        fluent: str,
        start: datetime,
        end: datetime,
        limit: int = SERIES_LIMIT,
    ) -> list[ContributionRecord]:
        """実体 × フルーエントの寄与を時刻順に返す（``start`` 以上 ``end`` 未満。右開）。

        Args:
            entity: 対象実体の IRI。
            fluent: フルーエント型 IRI。
            start: 区間の始端（含む）。
            end: 区間の終端（含まない）。
            limit: 返す件数の上限（超えたら警告する。黙って切らない）。

        Returns:
            寄与の列（時刻の昇順）。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT assertion_id, entity, fluent, at, prior, posterior, observed, record, "
                f"plan, source_event_id, excluded_records FROM {_TABLE} "
                "WHERE site = %s AND entity = %s AND fluent = %s AND at >= %s AND at < %s "
                "ORDER BY at ASC, assertion_id LIMIT %s",
                (self.site, entity, fluent, ensure_utc(start), ensure_utc(end), limit + 1),
            )
            rows = await cursor.fetchall()
        if len(rows) > limit:
            log.warning(
                "knowledge.contributions_truncated",
                site=self.site,
                entity=entity,
                limit=limit,
                detail="区間内の寄与が上限を超えた。先頭の分だけを返す（黙って切っていない）",
            )
        return [
            ContributionRecord(
                assertion_id=row[0] if isinstance(row[0], UUID) else UUID(str(row[0])),
                entity=str(row[1]),
                fluent=str(row[2]),
                at=ensure_utc(row[3]),
                prior=float(row[4]),
                posterior=float(row[5]),
                observed=float(row[6]),
                record=float(row[7]),
                plan=float(row[8]),
                source_event_id=str(row[9]),
                excluded_records=int(row[10]),
            )
            for row in rows[:limit]
        ]
