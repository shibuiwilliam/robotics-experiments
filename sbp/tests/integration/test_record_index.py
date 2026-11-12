"""業務記録の存在の投影（M8-27-2、ADR-0017）。

SQL（``ON CONFLICT DO NOTHING``、時刻の下限、拠点の分離）そのものなので、
実 Postgres でだけ確かめる。

重点:

1. **再送は増えない** — 取込は送信が失敗すると同じ記録を送り直す。
2. **期待の作成時刻より前の記録は数えない** — 検証は「アクションの後に記録が付いたか」を問う。
3. **対象が未解決の記録は入れない** — 実体で引けない行は検証の役に立たない。
4. **拠点が違えば見えない**。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.records import Record
from sbp.knowledge import RecordIndex

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

T0 = datetime(2026, 11, 1, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteRecIdx/asset/P-101"
WORK_ORDER = str(vocab.ent.WorkOrder)
LOG = str(vocab.ent.OperationLog)


def _record(
    *, external_id: str, at: datetime, record_type: str = WORK_ORDER, about: str | None
) -> Record:
    return Record(
        record_type=record_type,
        source=str(vocab.ent.CmmsSource),
        external_id=external_id,
        is_about=about,
        recorded_at=at,
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


class TestRecordIndex:
    @pytest.mark.asyncio
    async def test_records_are_found_by_entity_type_and_time(self, settings: Settings) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            index = RecordIndex(connection, site="siteRecIdx")
            await index.add(
                [
                    _record(external_id="WO-1", at=T0, about=PUMP),
                    _record(
                        external_id="L-1", at=T0 + timedelta(hours=1), record_type=LOG, about=PUMP
                    ),
                ]
            )
            assert await index.first_seen(PUMP, since=T0) == {
                WORK_ORDER: T0,
                LOG: T0 + timedelta(hours=1),
            }
            after = await index.first_seen(PUMP, since=T0 + timedelta(minutes=1))
            assert after == {LOG: T0 + timedelta(hours=1)}
            assert await index.first_seen(PUMP + "-x", since=T0) == {}
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_a_resend_adds_nothing_and_unresolved_records_are_skipped(
        self, settings: Settings
    ) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            index = RecordIndex(connection, site="siteRecIdx")
            batch = [
                _record(external_id="WO-1", at=T0, about=PUMP),
                _record(external_id="WO-2", at=T0, about=None),
            ]
            assert await index.add(batch) == 1
            assert await index.add(batch) == 0
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_another_site_does_not_see_the_rows(self, settings: Settings) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            await RecordIndex(connection, site="siteRecIdx").add(
                [_record(external_id="WO-1", at=T0, about=PUMP)]
            )
            other = RecordIndex(connection, site="siteOther")
            assert await other.first_seen(PUMP, since=T0) == {}
            await connection.rollback()
