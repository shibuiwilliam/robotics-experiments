"""信念の三源の寄与の投影（M8-17-2、ADR-0020）。

SQL（``ON CONFLICT DO NOTHING``、右開の時間範囲、拠点の分離、順序）そのものなので、
実 Postgres で確かめる。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from sbp.core import vocab
from sbp.core.config import Settings
from sbp.knowledge import ContributionRecord, ContributionStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SITE = "siteContributionTest"
T0 = datetime(2026, 11, 1, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteContributionTest/asset/P-101"
RUNNING = str(vocab.ent.Running)


def _record(
    *, at: datetime, observed: float = 1.0, record: float = 0.0, plan: float = 0.0
) -> ContributionRecord:
    return ContributionRecord(
        assertion_id=uuid4(),
        entity=PUMP,
        fluent=RUNNING,
        at=at,
        prior=0.5,
        posterior=0.8,
        observed=observed,
        record=record,
        plan=plan,
        source_event_id="evt-1",
        excluded_records=1,
    )


async def _cleanup(connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
    async with connection.cursor() as cursor:
        await cursor.execute("DELETE FROM belief_contributions WHERE site = %s", (SITE,))
    await connection.commit()


class TestContributionStore:
    @pytest.mark.asyncio
    async def test_a_written_record_round_trips(self, settings: Settings) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ContributionStore(connection, site=SITE)
            original = _record(at=T0, observed=2.0, record=1.0, plan=0.5)
            await store.write(original)
            found = await store.series(
                entity=PUMP, fluent=RUNNING, start=T0, end=T0 + timedelta(minutes=1)
            )
            assert found == [original]
            assert found[0].dominant == "observed"
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_the_same_assertion_is_written_once(self, settings: Settings) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ContributionStore(connection, site=SITE)
            original = _record(at=T0)
            await store.write(original)
            await store.write(original)
            found = await store.series(
                entity=PUMP, fluent=RUNNING, start=T0, end=T0 + timedelta(minutes=1)
            )
            assert len(found) == 1
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_the_range_is_right_open_and_ordered(self, settings: Settings) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ContributionStore(connection, site=SITE)
            for minutes in (3, 1, 2, 5):
                await store.write(_record(at=T0 + timedelta(minutes=minutes)))
            found = await store.series(
                entity=PUMP,
                fluent=RUNNING,
                start=T0 + timedelta(minutes=1),
                end=T0 + timedelta(minutes=5),
            )
            assert [row.at for row in found] == [T0 + timedelta(minutes=m) for m in (1, 2, 3)]
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_other_sites_entities_and_fluents_are_not_returned(
        self, settings: Settings
    ) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ContributionStore(connection, site=SITE)
            await store.write(_record(at=T0))
            end = T0 + timedelta(minutes=1)
            other_site = ContributionStore(connection, site="siteOther")
            assert await other_site.series(entity=PUMP, fluent=RUNNING, start=T0, end=end) == []
            assert await store.series(entity=PUMP + "-2", fluent=RUNNING, start=T0, end=end) == []
            assert await store.series(entity=PUMP, fluent="x", start=T0, end=end) == []
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_a_truncated_series_returns_the_earliest_rows(self, settings: Settings) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ContributionStore(connection, site=SITE)
            for minutes in range(5):
                await store.write(_record(at=T0 + timedelta(minutes=minutes)))
            found = await store.series(
                entity=PUMP, fluent=RUNNING, start=T0, end=T0 + timedelta(hours=1), limit=3
            )
            assert [row.at for row in found] == [T0 + timedelta(minutes=m) for m in range(3)]
            await _cleanup(connection)
