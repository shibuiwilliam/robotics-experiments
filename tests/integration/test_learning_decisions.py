"""人の判断から観測要求までを実物のサービス上で通す（M8-08、ADR-0012）。

**この経路が M8 で繰り返し出た「誰が最初の値を入れるか」の答えである**（`docs/PROGRESS.md`
所見 72）。人の判断 → 学習信号 → 観測要求の作業キュー、という流れを実物の Postgres で通す。

重点:

1. **判断は冪等** — UI の再送（ネットワークの再試行、人の二度押し）で 2 回適用されない。
2. **古い順に適用する** — 後の判断が前の判断を前提にしうる。
3. **失敗した判断は候補から外れるが行は残る** — 無限に再試行せず、人が再投入できる。
4. **「判定不能」が観測要求を積む** — M8-05 のキューに書く側がこれである。
"""

from __future__ import annotations

import psycopg
import pytest
from services.learning_worker import LearningWorker

from sbp.core.config import Settings
from sbp.core.time import now
from sbp.knowledge import DecisionKind, DecisionQueue, DecisionRow, ObservationQueue

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SITE = "siteLearningTest"
OPERATOR = "https://sbp.example/person/operator-e2e"
DISCREPANCY_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
"""不一致の識別子は UUID（所見 77）。"""
PUMP = "https://sbp.example/site/siteA/asset/P-101"


def _row(
    *,
    decision_id: str = "d-1",
    outcome: str = "undetermined",
    discrepancy_id: str = DISCREPANCY_ID,
) -> DecisionRow:
    return DecisionRow(
        decision_id=decision_id,
        kind=DecisionKind.RESOLUTION,
        subject=discrepancy_id,
        decided_by=OPERATOR,
        payload={
            "decision_id": decision_id,
            "discrepancy_id": discrepancy_id,
            "outcome": outcome,
            "note": "現場を見たが判断できない",
            "resolved_by": OPERATOR,
            "corrections": {},
        },
    )


async def _cleanup(connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
    """この拠点の行を落とす（どちらも投影表で、信念ではない）。"""
    async with connection.cursor() as cursor:
        await cursor.execute("DELETE FROM learning_decisions WHERE site = %s", (SITE,))
        await cursor.execute("DELETE FROM observation_requests WHERE site = %s", (SITE,))
    await connection.commit()


class TestQueueSemantics:
    """判断キューの意味（SQL）。"""

    @pytest.mark.asyncio
    async def test_the_same_decision_is_enqueued_once(self, settings: Settings) -> None:
        """同じ ``decision_id`` は 2 回積まれない。

        UI の再送で同じ判断が 2 回適用されると、書き戻しが 2 回走る。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = DecisionQueue(connection, site=SITE)
            assert await queue.enqueue(_row()) is True
            assert await queue.enqueue(_row()) is False

            assert len(await queue.pending()) == 1
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_pending_is_ordered_oldest_first(self, settings: Settings) -> None:
        """古い順に返る（後の判断が前の判断を前提にしうる）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = DecisionQueue(connection, site=SITE)
            for index in range(3):
                await queue.enqueue(_row(decision_id=f"d-{index}"))

            ids = [row.decision_id for row in await queue.pending()]
            assert ids == ["d-0", "d-1", "d-2"]
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_an_applied_decision_leaves_the_queue(self, settings: Settings) -> None:
        """適用した判断は候補から外れる。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = DecisionQueue(connection, site=SITE)
            await queue.enqueue(_row())
            await queue.mark_applied("d-1", at=now(), artifacts={"observation_requests": 1})

            assert await queue.pending() == []
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_a_failed_decision_is_not_retried_but_kept(self, settings: Settings) -> None:
        """失敗した判断は候補から外れるが行は残る。

        無限に再試行しない。人が理由を読んで再投入できる（ADR-0012 の未決）。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = DecisionQueue(connection, site=SITE)
            await queue.enqueue(_row())
            await queue.mark_failed("d-1", at=now(), error="業務システムが応答しない")

            assert await queue.pending() == []
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT error, attempts FROM learning_decisions WHERE decision_id = %s",
                    ("d-1",),
                )
                found = await cursor.fetchone()
            assert found is not None
            assert found[0] == "業務システムが応答しない"
            assert found[1] == 1
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_other_sites_are_not_returned(self, settings: Settings) -> None:
        """他拠点の判断は返らない。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            await DecisionQueue(connection, site=SITE).enqueue(_row())

            other = DecisionQueue(connection, site=f"{SITE}-other")
            assert await other.pending() == []
            await _cleanup(connection)


class TestEndToEnd:
    """判断 → 学習信号 → 観測要求。"""

    @pytest.mark.asyncio
    async def test_an_undetermined_resolution_enqueues_an_observation_request(
        self, settings: Settings
    ) -> None:
        """「判定不能」の解決が観測要求の作業キューに行く。

        **これが M8-05 の供給元である**——キューに書く側が無かったので、スケジューラは
        常に 0 件を返していた。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            decisions = DecisionQueue(connection, site=SITE)
            observations = ObservationQueue(connection, site=SITE)
            await decisions.enqueue(_row(outcome="undetermined"))

            worker = LearningWorker(
                settings,
                decisions=decisions,
                observations=observations.enqueue,
                period_s=0.0,
            )
            applied = await worker.step()

            assert applied == 1
            outstanding = await observations.outstanding()
            assert len(outstanding) >= 1, "観測要求が積まれていない"
            assert outstanding[0].discrepancy_id == DISCREPANCY_ID
            # 適用の要約が残る（UI が「この判断で何が起きたか」を読む）
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT artifacts FROM learning_decisions WHERE decision_id = %s", ("d-1",)
                )
                found = await cursor.fetchone()
            assert found is not None
            assert found[0]["observation_requests_enqueued"] >= 1
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_applying_twice_does_not_duplicate_requests(self, settings: Settings) -> None:
        """同じ判断を 2 周しても観測要求は増えない。

        判断は適用後に候補から外れる（2 周目は 0 件）。観測要求のキュー側も
        ``(不一致, フルーエント)`` で 1 行にまとまる。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            decisions = DecisionQueue(connection, site=SITE)
            observations = ObservationQueue(connection, site=SITE)
            await decisions.enqueue(_row(outcome="undetermined"))

            worker = LearningWorker(
                settings,
                decisions=decisions,
                observations=observations.enqueue,
                period_s=0.0,
            )
            await worker.step()
            first = len(await observations.outstanding())
            assert await worker.step() == 0

            assert len(await observations.outstanding()) == first
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_a_record_correct_resolution_records_bags(self, settings: Settings) -> None:
        """「記録が正しい」は負例バッグを**記録するだけ**（自動で再学習しない）。

        プローブの再学習は昇格ゲート（AUROC ∧ ECE ∧ 整合残差）を通る別の仕事である。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            decisions = DecisionQueue(connection, site=SITE)
            await decisions.enqueue(_row(outcome="record_correct"))

            worker = LearningWorker(settings, decisions=decisions, period_s=0.0)
            await worker.step()

            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT artifacts FROM learning_decisions WHERE decision_id = %s", ("d-1",)
                )
                found = await cursor.fetchone()
            assert found is not None
            assert "bags_recorded" in found[0]
            await _cleanup(connection)
