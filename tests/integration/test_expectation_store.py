"""期待の作業項目表（M8-18）。

**ここでしか確かめられない。** `ExpectationStore` は SQL（後退を弾く UPSERT、
`deadline NULLS LAST` の並べ替え、部分索引）そのものである。

重点:

1. **期限の近い順**（期限が無いものは後ろ）— 期待ボードが最初に見る順序。
2. **ライフサイクルを後退させられない** — 検証済みの期待がボードに戻らない。
3. **3 つの判定は同じ位置** — `Fulfilled` の行を `Violated` で上書きできる（互いに遷移しない
   ので後退ではない）が、`Active` には戻せない。
4. **終端状態を既定で除く** — 履歴でボードが埋まらない。
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

from sbp.core.config import Settings
from sbp.core.errors import KnowledgeError
from sbp.core.time import now
from sbp.knowledge import ExpectationStore
from sbp.reasoning.expectation import (
    Expectation,
    ExpectationStatus,
    ExpectedEffect,
    VerificationMethod,
    VerificationResult,
)
from sbp.reasoning.expectation_store import from_record, to_record

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SITE = "siteExpectationTest"
PUMP = "https://sbp.example/site/siteA/asset/P-101"
LINE = "https://sbp.example/site/siteA/asset/LINE-1"
ACTION = "https://sbp.example/ent#CreateEmergencyInspection"
RUNNING = "https://sbp.example/ent#Running"


def _expectation(*, minutes: int = 60, target: str = PUMP, effects: bool = True) -> Expectation:
    created = now().replace(microsecond=0)
    declared = (
        (
            ExpectedEffect(
                fluent=RUNNING,
                entity=target,
                expected=0.9,
                lower=0.8,
                upper=1.0,
                deadline=created + timedelta(minutes=minutes),
                origin="symbolic",
            ),
        )
        if effects
        else ()
    )
    return Expectation(
        action_event=uuid4(),
        action_type=ACTION,
        target=target,
        expected_effects=declared,
        status=ExpectationStatus.ACTIVE if effects else ExpectationStatus.CREATED,
        created_at=created,
        ontology_version="0.5.0",
    )


async def _cleanup(connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
    async with connection.cursor() as cursor:
        await cursor.execute("DELETE FROM expectations WHERE site = %s", (SITE,))
    await connection.commit()


class TestWriteAndRead:
    """書く・読む。"""

    @pytest.mark.asyncio
    async def test_a_written_expectation_round_trips(self, settings: Settings) -> None:
        """書いた期待が本体ごと戻る（期待効果も保たれる）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ExpectationStore(connection, site=SITE)
            original = _expectation()
            await store.upsert(to_record(original))

            record = await store.get(original.expectation_id)
            assert record is not None
            assert from_record(record) == original
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_the_board_is_ordered_by_deadline(self, settings: Settings) -> None:
        """期限の近い順（期待ボードが最初に見る順序）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ExpectationStore(connection, site=SITE)
            for minutes in (120, 30, 60):
                await store.upsert(to_record(_expectation(minutes=minutes)))

            deadlines = [record.deadline for record in await store.board()]
            assert deadlines == sorted(d for d in deadlines if d is not None)
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_expectations_without_a_deadline_go_last(self, settings: Settings) -> None:
        """期限が無い期待（``Created``）は後ろに置く。

        「期限切れが近い」判断の対象にならない——先頭に来ると、効果をまだ持たない期待が
        最優先に見える。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ExpectationStore(connection, site=SITE)
            await store.upsert(to_record(_expectation(effects=False)))
            await store.upsert(to_record(_expectation(minutes=90)))

            board = await store.board()
            assert len(board) == 2
            assert board[0].deadline is not None
            assert board[-1].deadline is None
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_the_board_can_filter_by_target(self, settings: Settings) -> None:
        """対象実体で絞れる（信念ビューから飛ぶ）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ExpectationStore(connection, site=SITE)
            await store.upsert(to_record(_expectation(target=PUMP)))
            await store.upsert(to_record(_expectation(target=LINE)))

            assert [r.target for r in await store.board(target=LINE)] == [LINE]
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_other_sites_are_not_returned(self, settings: Settings) -> None:
        """他拠点の期待は返らない。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            await ExpectationStore(connection, site=SITE).upsert(to_record(_expectation()))

            other = ExpectationStore(connection, site=f"{SITE}-other")
            assert await other.board() == []
            await _cleanup(connection)


class TestLifecycle:
    """状態の進行と後退。"""

    @pytest.mark.asyncio
    async def test_a_verdict_is_recorded(self, settings: Settings) -> None:
        """検証結果が反映される。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ExpectationStore(connection, site=SITE)
            original = _expectation()
            await store.upsert(to_record(original))

            verified = original.verify(
                VerificationResult(
                    method=VerificationMethod.CONFORMAL,
                    verified_at=now(),
                    symbolic_passed=True,
                    note="被覆検定を通った",
                ),
                at=now(),
            )
            await store.upsert(to_record(verified))

            record = await store.get(original.expectation_id)
            assert record is not None
            assert record.status == "Fulfilled"
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_a_verdict_cannot_go_back_to_active(self, settings: Settings) -> None:
        """判定が出た期待を ``Active`` に戻せない。

        複数の経路が同じ行を触るので、古い状態での上書きを表の側でも弾く（所見 80）。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ExpectationStore(connection, site=SITE)
            original = _expectation()
            verified = original.verify(
                VerificationResult(
                    method=VerificationMethod.CONFORMAL,
                    verified_at=now(),
                    symbolic_passed=True,
                ),
                at=now(),
            )
            await store.upsert(to_record(verified))

            with pytest.raises(KnowledgeError, match="backwards"):
                await store.upsert(to_record(original))
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_a_terminal_expectation_leaves_the_board(self, settings: Settings) -> None:
        """終端状態（``Learned``）はボードから外れるが履歴には残る。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ExpectationStore(connection, site=SITE)
            original = _expectation()
            learned = original.verify(
                VerificationResult(
                    method=VerificationMethod.CONFORMAL,
                    verified_at=now(),
                    symbolic_passed=True,
                ),
                at=now(),
            ).mark_learned(at=now(), note="介入データとして登録した")
            await store.upsert(to_record(learned))

            assert await store.board() == []
            assert len(await store.board(include_terminal=True)) == 1
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_counts_by_status(self, settings: Settings) -> None:
        """状態ごとの件数が引ける（運用の棚卸しと指標）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ExpectationStore(connection, site=SITE)
            await store.upsert(to_record(_expectation()))
            await store.upsert(to_record(_expectation(effects=False)))

            assert await store.count_by_status() == {"Active": 1, "Created": 1}
            await _cleanup(connection)
