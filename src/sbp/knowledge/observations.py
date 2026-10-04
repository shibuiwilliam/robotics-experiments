"""追加観測の要求の作業キュー（M8-05、ADR-0011）。

照合の解決が「判断できない」だったとき追加観測の要求が出る（M3-06）。エピステミック・
スケジューラは周期ごとにこれを評価して観測クエリを発するが、**要求を溜める場所**がこれである。

**イベントログには載せられない**（ADR-0011）。``EventEnvelope`` は「誰かの主張」を運ぶ形で、
様相と信念を必須に持つ。追加観測の要求は主張ではなく指令であり、様相格子（``observed`` /
``inferred`` / ``predicted`` / ``planned`` / ``normative`` / ``hypothetical``）に席が無い。

**既存の ``quarantine`` と同じ形の作業キューである。** 世界の状態（信念）ではなく作業の状態を
持つので、「状態はイベントの投影」の規律の対象外になる。

**行を消さない。** 満たされた要求も ``satisfied_at`` を立てるだけで残す——「観測を要求したが
満たされなかった」履歴が消えると、スケジューラが機能しているかどうかを後から確かめられない
（所見 32: 「0 件」と「経路が動いていない」を区別する）。

**``ObservationRequest`` を知らない。** その型は ``reasoning`` にあり、``knowledge`` は下位層
なので import できない（層依存の向き）。この層は :class:`ObservationRow` を扱い、
``reasoning`` の型への変換はワーカー（``services/``）が行う——潜在索引の読み出しが
``services`` 側にあるのと同じ形で、層の境界がワーカーの形に出る。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sbp.core.logging import get_logger
from sbp.core.time import Interval, ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from psycopg import AsyncConnection

__all__ = ["ObservationQueue", "ObservationRow"]

log = get_logger(__name__)

_TABLE = "observation_requests"


@dataclass(frozen=True, slots=True)
class ObservationRow:
    """キューの 1 行。

    ``sbp.reasoning.reconciliation.learning.ObservationRequest`` と同じ情報を持つが、
    **こちらは ``knowledge`` 層の型である**（下位層が上位層の型を import できないため）。
    変換はワーカーが行う。

    Attributes:
        entity: 観測したい実体の IRI。
        fluent: 確かめたいフルーエントの IRI。
        interval: 対象区間（右開）。
        reason: なぜ追加観測が必要か。
        utility: 期待効用のヒント（照合が付けた深刻度）。
        discrepancy_id: 由来の不一致。
    """

    entity: str
    fluent: str
    interval: Interval
    reason: str
    utility: float
    discrepancy_id: str


class ObservationQueue:
    """追加観測の要求の読み書き。

    Attributes:
        site: 拠点識別子（1 プロセス 1 拠点。要求は拠点ごとに溜まる）。
    """

    def __init__(self, connection: AsyncConnection, *, site: str) -> None:
        """Args:
        connection: Postgres 接続（async）。
        site: 拠点識別子。
        """
        self._connection = connection
        self.site = site

    @staticmethod
    def request_id(*, discrepancy_id: str, fluent: str) -> str:
        """要求の識別子。

        **同じ不一致・同じフルーエントの要求は 1 行にまとめる。** 照合は周期ごとに回るので、
        解決されない不一致からは同じ要求が繰り返し出る。毎回新しい行を作ると、キューが
        周期の回数だけ膨らみ、スケジューラが同じ観測を何度も選ぶ。

        Args:
            discrepancy_id: 由来の不一致。
            fluent: 対象フルーエント IRI。
        """
        return f"{discrepancy_id}:{fluent}"

    async def enqueue(self, requests: Sequence[ObservationRow]) -> int:
        """要求を積む（同じ識別子は効用と理由を更新するだけ）。

        **満たされた要求を未充足に戻さない。** 同じ不一致から再度要求が出ても、
        ``satisfied_at`` が立っている行は触らない——観測は済んでいるので、もう一度測っても
        同じ答えが返る（不一致が消えないのは観測の問題ではない）。

        Args:
            requests: 積む要求。

        Returns:
            新しく積まれた、または更新された件数。
        """
        if not requests:
            return 0
        rows = [
            (
                self.request_id(discrepancy_id=request.discrepancy_id, fluent=request.fluent),
                self.site,
                request.entity,
                request.fluent,
                ensure_utc(request.interval.start),
                None if request.interval.end is None else ensure_utc(request.interval.end),
                request.reason,
                request.utility,
                request.discrepancy_id,
            )
            for request in requests
        ]
        async with self._connection.cursor() as cursor:
            await cursor.executemany(
                f"INSERT INTO {_TABLE} "
                "(request_id, site, entity, fluent, valid_from, valid_to, reason, utility, "
                "discrepancy_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (request_id) DO UPDATE "
                "SET utility = EXCLUDED.utility, reason = EXCLUDED.reason "
                f"WHERE {_TABLE}.satisfied_at IS NULL",
                rows,
            )
        log.info("knowledge.observation_requests_enqueued", site=self.site, count=len(rows))
        return len(rows)

    async def outstanding(self, *, limit: int = 200) -> list[ObservationRow]:
        """まだ満たされていない要求を効用の高い順に返す。

        **発行済み（``issued_at`` が立っている）ものも返す。** 観測クエリを発しても結果が
        戻るとは限らず、戻ってこなかった要求を候補から外すと永久に満たされない。スケジューラが
        同じ要求を再び選ぶのは正しい（効用は信念が動けば変わる）。

        Args:
            limit: 返す件数の上限。**上限を置くのは効用計算が件数に比例するため**。
                打ち切ったことはログに出す。

        Returns:
            要求の列（効用の降順、同値なら古い順）。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "SELECT entity, fluent, valid_from, valid_to, reason, utility, discrepancy_id "
                f"FROM {_TABLE} WHERE site = %s AND satisfied_at IS NULL "
                "ORDER BY utility DESC, requested_at LIMIT %s",
                (self.site, limit + 1),
            )
            rows = await cursor.fetchall()
        truncated = len(rows) > limit
        found = [
            ObservationRow(
                entity=str(row[0]),
                fluent=str(row[1]),
                interval=Interval(
                    ensure_utc(row[2]), None if row[3] is None else ensure_utc(row[3])
                ),
                reason=str(row[4]),
                utility=float(row[5]),
                discrepancy_id=str(row[6]),
            )
            for row in rows[:limit]
        ]
        if truncated:
            log.warning(
                "knowledge.observation_requests_truncated",
                site=self.site,
                limit=limit,
                detail="未充足の要求が上限を超えた。選択の候補が欠けている（黙って切っていない）",
            )
        log.debug(
            "knowledge.observation_requests_read",
            site=self.site,
            found=len(found),
            truncated=truncated,
        )
        return found

    async def mark_issued(
        self, *, discrepancy_id: str, fluent: str, query_id: str, at: datetime
    ) -> None:
        """観測クエリを発したことを記録する。

        **未充足のまま残す。** 発行は「頼んだ」であって「分かった」ではない。満たされたかどうかは
        結果が ``observed`` で戻ってきたときに :meth:`mark_satisfied` が立てる。

        Args:
            discrepancy_id: 由来の不一致。
            fluent: 対象フルーエント。
            query_id: 発したクエリの識別子。
            at: 発行時刻。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"UPDATE {_TABLE} SET issued_at = %s, issued_query_id = %s "
                "WHERE request_id = %s AND site = %s",
                (
                    ensure_utc(at),
                    query_id,
                    self.request_id(discrepancy_id=discrepancy_id, fluent=fluent),
                    self.site,
                ),
            )

    async def mark_satisfied(self, *, discrepancy_id: str, fluent: str, at: datetime) -> None:
        """要求が満たされたことを記録する（行は消さない）。

        **呼ぶ側は未実装である**（ADR-0011 の未決）。``observed`` のイベントが戻ってきたときに
        どの要求を満たしたかを突き合わせる処理が要り、「どれだけ測れば満たされたか」は手段に
        よって違う。API だけ用意しておく。

        Args:
            discrepancy_id: 由来の不一致。
            fluent: 対象フルーエント。
            at: 充足時刻。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"UPDATE {_TABLE} SET satisfied_at = %s "
                "WHERE request_id = %s AND site = %s AND satisfied_at IS NULL",
                (
                    ensure_utc(at),
                    self.request_id(discrepancy_id=discrepancy_id, fluent=fluent),
                    self.site,
                ),
            )
        log.info(
            "knowledge.observation_request_satisfied",
            site=self.site,
            discrepancy_id=discrepancy_id,
            fluent=fluent,
        )
