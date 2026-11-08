"""隔離キュー（M10-01、ADR-0035）。

**ここでしか確かめられない。** 冪等（同じ入力・同じ理由は 1 行）、確認済みを開き直さない、
拠点の分離、は SQL の条件そのものなので、実 Postgres で確かめる。
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
import pytest

from sbp.core.config import Settings
from sbp.core.time import now
from sbp.knowledge import QuarantineStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SITE = "siteQuarantineTest"
SOURCE = "https://sbp.example/ent#CmmsSource"


@dataclass(frozen=True)
class _Violation:
    source_shape: str
    message: str
    focus_node: str
    path: str | None


async def _add(store: QuarantineStore, subject: str = "X-1", reason: str = "bad type") -> bool:
    return await store.add(
        source=SOURCE,
        table="asset_register",
        subject=subject,
        reason=reason,
        payload={"fields": {"asset_type": "Gizmo"}},
    )


class TestQuarantineStore:
    @pytest.mark.asyncio
    async def test_an_added_row_comes_back_with_its_raw_fields(self, settings: Settings) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = QuarantineStore(connection, site=SITE)
            assert await _add(store)
            rows = await store.recent()
            assert [(r.subject, r.reason) for r in rows] == [("X-1", "bad type")]
            assert rows[0].payload["fields"] == {"asset_type": "Gizmo"}, "生の入力を捨てない"
            assert rows[0].topic == "ingest:asset_register"
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_the_same_input_for_the_same_reason_is_one_row(self, settings: Settings) -> None:
        """台帳は毎周期全行を読む。落ち続けても 1 行。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = QuarantineStore(connection, site=SITE)
            assert await _add(store) is True
            assert await _add(store) is False
            assert len(await store.recent()) == 1
            assert await _add(store, reason="another reason") is True, "理由が変われば別の行"
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_a_reviewed_row_is_not_reopened_by_the_same_failure(
        self, settings: Settings
    ) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = QuarantineStore(connection, site=SITE)
            await _add(store)
            [row] = await store.recent()
            assert await store.mark_reviewed(row.event_id, resolution="対応不要", at=now())
            assert await _add(store) is False
            assert await store.recent() == [], "確認済みは未確認の一覧に戻らない"
            [reviewed] = await store.recent(only_unreviewed=False)
            assert reviewed.resolution == "対応不要"
            assert reviewed.reviewed_at is not None
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_review_is_once_only(self, settings: Settings) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = QuarantineStore(connection, site=SITE)
            await _add(store)
            [row] = await store.recent()
            assert await store.mark_reviewed(row.event_id, resolution="first", at=now())
            assert not await store.mark_reviewed(row.event_id, resolution="second", at=now())
            [reviewed] = await store.recent(only_unreviewed=False)
            assert reviewed.resolution == "first"
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_sites_are_separate(self, settings: Settings) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            mine = QuarantineStore(connection, site=SITE)
            other = QuarantineStore(connection, site=SITE + "-other")
            await _add(mine)
            assert await other.recent() == []
            [row] = await mine.recent()
            assert not await other.mark_reviewed(row.event_id, resolution="x", at=now()), (
                "他拠点の隔離は確認できない"
            )
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_shacl_violations_are_queued_but_warnings_are_not_passed_in(
        self, settings: Settings
    ) -> None:
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = QuarantineStore(connection, site=SITE)
            violation = _Violation(
                "https://sbp.example/core#AssetShape",
                "Asset must have at least one locatedIn",
                "https://sbp.example/site/siteA/asset/Z",
                "https://sbp.example/core#locatedIn",
            )
            added = await store.add_violations(
                [violation, violation], source=SOURCE, table="asset", payload={"graph": "ttl"}
            )
            assert added == 1, "同じ違反は 1 行"
            [row] = await store.recent()
            assert row.payload["path"] == "https://sbp.example/core#locatedIn"
            await connection.rollback()
