"""概念提案の作業項目の読み書き（M8-19、ADR-0015）。

不一致（ADR-0013）と期待（M8-18）と同じ形である——提案も採否の状態を持つ作業項目で、
``EventEnvelope`` に全体が入らない（``signals`` / ``rejected_alternatives`` を入れる場所が無い。
``docs/PROGRESS.md`` 所見 62）。

**潜在の重心を持たない**（ADR-0015）。`LatentCluster.centroid` は潜在索引のもので、ここに置くと
再接地で片方だけが古くなる。代わりに**判断に要る 2 つのスカラー**（``support_count`` /
``coverage``）を列に出す——UI の curation 検査（設計書 §11.2）がこれを使い、重心を落とした
読み出し側では計算できない。

**``ConceptProposal`` の型を知らない**（層依存の向き）。:class:`ConceptProposalRecord` を扱い、
変換は :mod:`sbp.reasoning.concept_store` が行う。
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
    from psycopg import AsyncConnection

__all__ = ["ConceptProposalRecord", "ConceptProposalStore"]

log = get_logger(__name__)

_TABLE = "concept_proposals"

_PENDING_STATUS = "proposed"
"""まだ人が採否を決めていない状態（``ProposalStatus.PROPOSED`` の値）。

**``reasoning`` の列挙を import できない**ので値を写している。食い違うと curation の待ち行列が
空になる（または全件出る）ため、単体テストで両者の一致を検査する。
"""


@dataclass(frozen=True, slots=True)
class ConceptProposalRecord:
    """表の 1 行（問い合わせ用の列 ＋ 本体）。

    Attributes:
        proposal_id: 識別子。
        name: ローカル名（同じ名前の提案を探すのに使う）。
        status: 採否の値（``proposed`` など）。
        status_rank: ライフサイクル上の位置（``ProposalStatus.rank``）。
        superclass: 上位概念のローカル名。
        support_count: 支持する標本数（**書き込み時に導出**。設計書 §11.2 の判断材料）。
        coverage: 既存概念での被覆率（**書き込み時に導出**。同上）。
        proposed_at: 提案時刻（``None`` なら未記録）。
        payload: 本体（重心を落とした提案の dict）。
        updated_at: 最後に行を更新した時刻（採否を入れた時刻。読み出し専用で、``upsert`` は
            使わない。却下の冷却期間の起点。ADR-0019）。
    """

    proposal_id: UUID
    name: str
    status: str
    status_rank: int
    superclass: str = ""
    support_count: int = 0
    coverage: float = 0.0
    proposed_at: datetime | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime | None = None

    @property
    def is_pending(self) -> bool:
        """人の採否を待っているか（curation の待ち行列に出るか）。"""
        return self.status == _PENDING_STATUS


class ConceptProposalStore:
    """概念提案の作業項目の読み書き。

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

    async def upsert(self, record: ConceptProposalRecord) -> None:
        """提案を書く（同じ識別子は更新）。

        Args:
            record: 書く提案。

        Raises:
            KnowledgeError: 採否が入った提案を ``proposed`` に戻そうとした場合。
                **黙って無視しない**——判断済みの提案が curation の待ち行列に戻ったように見える。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO {_TABLE} "
                "(proposal_id, site, name, status, status_rank, superclass, support_count, "
                "coverage, proposed_at, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (proposal_id) DO UPDATE SET "
                "status = EXCLUDED.status, status_rank = EXCLUDED.status_rank, "
                "payload = EXCLUDED.payload, updated_at = now() "
                f"WHERE {_TABLE}.status_rank <= EXCLUDED.status_rank",
                (
                    str(record.proposal_id),
                    self.site,
                    record.name,
                    record.status,
                    record.status_rank,
                    record.superclass,
                    record.support_count,
                    record.coverage,
                    None if record.proposed_at is None else ensure_utc(record.proposed_at),
                    json.dumps(record.payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            written = cursor.rowcount
        if written == 0:
            raise KnowledgeError(
                f"concept proposal {record.proposal_id} has already been curated; refusing to "
                f"move it back to {record.status!r} (rank {record.status_rank}). "
                "Redo a judgement by issuing a new proposal, not by reopening this one (ADR-0015)."
            )
        log.info(
            "knowledge.concept_proposal_written",
            site=self.site,
            correlation_id=str(record.proposal_id),
            status=record.status,
            name=record.name,
        )

    async def curate(self, proposal_id: UUID | str, *, status: str, rank: int, note: str) -> bool:
        """採否だけを更新する（本体を読まずに状態を進める）。

        **learning-worker が人の判断を適用するときに使う。** 提案の本体を読み戻さなくても
        状態を進められる——読み戻すと重心が復元されない提案を書き直すことになり、payload が
        劣化する。

        Args:
            proposal_id: 識別子。
            status: 新しい採否の値。
            rank: その採否の ``ProposalStatus.rank``。
            note: 採否の理由（payload の ``curation_note`` に入る）。

        Returns:
            更新できたか（``False`` なら行が無い、または既に採否が入っている）。
            **例外にしない**——人の判断は失わせず、呼び出し側に「見つからなかった」ことを返す。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"UPDATE {_TABLE} SET status = %s, status_rank = %s, "
                "payload = jsonb_set("
                "  jsonb_set(payload, '{status}', to_jsonb(%s::text)),"
                "  '{curation_note}', to_jsonb(%s::text)), "
                "updated_at = now() "
                # **厳密な `<` である。** `<=` にすると、既に採否が入った提案（rank 1）を
                # 別の採否（同じ rank 1）で上書きできてしまう——2 人が別の判断を入れたとき、
                # 後の判断が黙って勝つ（所見 96）。`upsert` の `<=` は同じ状態の書き直しを
                # 許すためで、ここは「未判断から判断へ」の遷移なので同値を許さない
                "WHERE proposal_id = %s AND site = %s AND status_rank < %s",
                (
                    status,
                    rank,
                    status,
                    note,
                    str(_as_uuid(proposal_id)),
                    self.site,
                    rank,
                ),
            )
            updated = cursor.rowcount
        log.info(
            "knowledge.concept_proposal_curated",
            site=self.site,
            correlation_id=str(proposal_id),
            status=status,
            updated=updated,
        )
        return updated > 0

    async def get(self, proposal_id: UUID | str) -> ConceptProposalRecord | None:
        """識別子で 1 件引く。

        Args:
            proposal_id: 識別子。

        Returns:
            行、無ければ ``None``（例外にしない）。

        Raises:
            KnowledgeError: 識別子が UUID でない場合。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"SELECT {_COLUMNS} FROM {_TABLE} WHERE proposal_id = %s AND site = %s",
                (str(_as_uuid(proposal_id)), self.site),
            )
            row = await cursor.fetchone()
        return None if row is None else _to_record(row)

    async def queue(
        self, *, limit: int = 50, include_curated: bool = False
    ) -> list[ConceptProposalRecord]:
        """curation の待ち行列（支持の多い順）。

        **支持する標本の多い提案を先に出す。** 偶然のクラスタと区別できない提案（標本が少ない）
        を先頭に置くと、人の判断の時間が弱い提案に使われる（設計書 §11.2）。

        Args:
            limit: 返す件数の上限。
            include_curated: 採否が入ったものも含めるか。

        Returns:
            行の列（支持の降順、同値なら新しい順）。
        """
        conditions = [sql.SQL("site = %s")]
        params: list[object] = [self.site]
        if not include_curated:
            conditions.append(sql.SQL("status = %s"))
            params.append(_PENDING_STATUS)
        params.append(limit)
        statement = sql.SQL(
            "SELECT {columns} FROM {table} WHERE {where} "
            "ORDER BY support_count DESC, proposed_at DESC NULLS LAST LIMIT %s"
        ).format(
            columns=sql.SQL(_COLUMNS),
            table=sql.Identifier(_TABLE),
            where=sql.SQL(" AND ").join(conditions),
        )
        async with self._connection.cursor() as cursor:
            await cursor.execute(statement, tuple(params))
            rows = await cursor.fetchall()
        found = [_to_record(row) for row in rows]
        log.debug(
            "knowledge.concept_queue_read",
            site=self.site,
            found=len(found),
            include_curated=include_curated,
        )
        return found

    async def count_by_status(self) -> dict[str, int]:
        """採否ごとの件数（運用の棚卸しと指標に使う）。

        Returns:
            採否 → 件数。
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                f"SELECT status, count(*) FROM {_TABLE} WHERE site = %s GROUP BY status",
                (self.site,),
            )
            rows = await cursor.fetchall()
        return {str(row[0]): int(row[1]) for row in rows}


_COLUMNS = (
    "proposal_id, name, status, status_rank, superclass, support_count, coverage, "
    "proposed_at, payload, updated_at"
)
"""読み出す列（並び順を :func:`_to_record` と一致させる）。"""


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
    except ValueError as error:
        raise KnowledgeError(
            f"concept proposal id {value!r} is not a UUID; the identifier type must match the "
            "column so the curation decision can be traced back to its proposal"
        ) from error


def _to_record(row: tuple[Any, ...]) -> ConceptProposalRecord:
    """行を :class:`ConceptProposalRecord` にする。

    Args:
        row: ``_COLUMNS`` の順の値。
    """
    payload = row[8] if isinstance(row[8], dict) else json.loads(row[8])
    return ConceptProposalRecord(
        proposal_id=row[0] if isinstance(row[0], UUID) else UUID(str(row[0])),
        name=str(row[1]),
        status=str(row[2]),
        status_rank=int(row[3]),
        superclass=str(row[4]),
        support_count=int(row[5]),
        coverage=float(row[6]),
        proposed_at=row[7],
        payload=payload,
        updated_at=row[9],
    )
