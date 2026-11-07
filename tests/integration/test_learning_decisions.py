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

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from services.learning_worker import LearningWorker

from sbp.actions import ActionRegistry
from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.time import Interval, now
from sbp.knowledge import (
    DecisionKind,
    DecisionQueue,
    DecisionRow,
    DiscrepancyStore,
    ExpectationStore,
    ObservationQueue,
)
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    Severity,
    to_record,
)

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
    async def test_an_answer_decision_is_accepted_by_the_table(self, settings: Settings) -> None:
        """``kind = answer``（ADR-0018、alembic 0012）が表の CHECK を通る。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = DecisionQueue(connection, site=SITE)
            row = DecisionRow(
                decision_id="a-1",
                kind=DecisionKind.ANSWER,
                subject=PUMP,
                decided_by=OPERATOR,
                payload={"state": True},
            )
            assert await queue.enqueue(row) is True
            assert [item.kind for item in await queue.pending()] == [DecisionKind.ANSWER]
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_history_returns_every_state_for_a_subject_newest_first(
        self, settings: Settings
    ) -> None:
        """適用済み・失敗（理由付き）・待ちを主語で引く（M8-22、ADR-0021）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            queue = DecisionQueue(connection, site=SITE)
            for decision_id in ("h-applied", "h-failed", "h-pending"):
                await queue.enqueue(
                    DecisionRow(
                        decision_id=decision_id,
                        kind=DecisionKind.ACTION,
                        subject=PUMP,
                        decided_by=OPERATOR,
                        payload={},
                    )
                )
            await queue.enqueue(
                DecisionRow(
                    decision_id="h-other",
                    kind=DecisionKind.ACTION,
                    subject=PUMP + "-2",
                    decided_by=OPERATOR,
                    payload={},
                )
            )
            await queue.mark_applied("h-applied", at=now(), artifacts={"expectation_id": "e-1"})
            await queue.mark_failed("h-failed", at=now(), error="ActionError: rate limit")

            found = await queue.history(subject=PUMP)
            by_id = {row.decision_id: row for row in found}
            assert set(by_id) == {"h-applied", "h-failed", "h-pending"}
            assert by_id["h-applied"].state == "applied"
            assert by_id["h-applied"].artifacts == {"expectation_id": "e-1"}
            assert by_id["h-failed"].state == "failed"
            assert by_id["h-failed"].error == "ActionError: rate limit"
            assert by_id["h-pending"].state == "pending"
            received = [row.received_at for row in found]
            assert received == sorted(received, reverse=True)

            only_actions = await queue.history(subject=PUMP, kinds=[DecisionKind.CURATION])
            assert only_actions == []
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


class TestDiscrepancyBodyIsUsed:
    """不一致の本体を読んで学習信号を作る（M8-12 が M8-08 の回避を解消した）。

    M8-08 の時点では本体を読む先が無く、判断の行から**最小の不一致を再構成**していた
    （証拠を持たないので記録訂正の提案が出ない）。本体が読めれば証拠 IRI を持つので、
    「物理が正しい」の解決が記録訂正の提案を出せる。
    """

    @pytest.mark.asyncio
    async def test_the_body_is_read_from_the_store(self, settings: Settings) -> None:
        """表に本体があれば ``discrepancy_source`` が ``store`` になる。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)
            decisions = DecisionQueue(connection, site=SITE)
            observations = ObservationQueue(connection, site=SITE)

            start = now().replace(microsecond=0) - timedelta(hours=1)
            discrepancy = Discrepancy(
                discrepancy_id=UUID(DISCREPANCY_ID),
                kind=DiscrepancyKind.STATE,
                subjects=(PUMP,),
                assertions=("urn:sbp:assertion:1",),
                evidence=("urn:sbp:record:A",),
                detected_at=start,
                interval=Interval(start, start + timedelta(minutes=10)),
                severity=Severity(
                    score=0.6, value_at_stake=1000.0, confidence=0.88, persistence_s=600.0
                ),
                detector="kind2",
                ontology_version="0.5.0",
            )
            await store.upsert(to_record(discrepancy))
            await decisions.enqueue(_row(outcome="undetermined"))

            worker = LearningWorker(
                settings,
                decisions=decisions,
                discrepancies=store.get,
                observations=observations.enqueue,
                period_s=0.0,
            )
            assert await worker.step() == 1

            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT artifacts FROM learning_decisions WHERE decision_id = %s", ("d-1",)
                )
                found = await cursor.fetchone()
            assert found is not None
            assert found[0]["discrepancy_source"] == "store", "本体が読まれていない"

            # 観測要求が本体の確信度と区間を引き継ぐ（再構成では確信度 0 になる）
            outstanding = await observations.outstanding()
            assert outstanding
            assert outstanding[0].utility > 0.0, "深刻度が本体から来ていない"

            async with connection.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM discrepancies WHERE discrepancy_id = %s", (DISCREPANCY_ID,)
                )
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_a_missing_body_falls_back_and_says_so(self, settings: Settings) -> None:
        """本体が無ければ代替し、代替したことを要約に出す。

        「訂正が 0 件」の理由が「訂正不要」なのか「証拠が無い」のか区別できる必要がある。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)
            decisions = DecisionQueue(connection, site=SITE)
            await decisions.enqueue(_row(outcome="undetermined"))

            worker = LearningWorker(
                settings, decisions=decisions, discrepancies=store.get, period_s=0.0
            )
            assert await worker.step() == 1

            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT artifacts FROM learning_decisions WHERE decision_id = %s", ("d-1",)
                )
                found = await cursor.fetchone()
            assert found is not None
            assert found[0]["discrepancy_source"] == "reconstructed"
            await _cleanup(connection)


class TestActionExecution:
    """承認されたアクションの実行（M8-21、ADR-0014）。

    **ここで M5 の閉ループが運用経路に乗る。** 人が承認 → キュー → ワーカーが設計書 §9.2 の
    順序で適用 → 期待が `expectations` 表に残る。これまで実行はライブラリとテストの中にしか
    無かった（所見 89）。
    """

    @staticmethod
    def _action_row(*, decision_id: str = "a-1", override: str | None = None) -> DecisionRow:
        payload: dict[str, object] = {
            "decision_id": decision_id,
            "action_type": vocab.ent.CreateEmergencyInspection,
            "target": PUMP,
            "approved_by": OPERATOR,
            "reason": "停止の疑いが強く、緊急点検を起票する",
            "severity": 0.9,
            "correlation_id": DISCREPANCY_ID,
        }
        if override is not None:
            payload["override_reason"] = override
        return DecisionRow(
            decision_id=decision_id,
            kind=DecisionKind.ACTION,
            subject=PUMP,
            decided_by=OPERATOR,
            payload=payload,
        )

    @staticmethod
    def _satisfying_beliefs() -> dict[str, float]:
        """緊急点検の物理前提を満たす信念（契約から作る）。"""
        contracts = ActionRegistry.from_directory(Path("actions"))
        beliefs: dict[str, float] = {}
        for condition in contracts.get(vocab.ent.CreateEmergencyInspection).all_preconditions:
            if condition.fluent is None or condition.threshold is None:
                continue
            beliefs[condition.fluent] = (
                condition.threshold if condition.kind == "belief_at_least" else 0.0
            )
        return beliefs

    @pytest.mark.asyncio
    async def test_an_approved_action_persists_its_expectation(self, settings: Settings) -> None:
        """承認されたアクションが期待を表に残す（設計書 §9.2 の 1→4）。

        **表に残ることが要点である。** 期待が残らなければ効果の検証ができず、学習が止まる。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            decisions = DecisionQueue(connection, site=SITE)
            expectations = ExpectationStore(connection, site=SITE)
            await decisions.enqueue(self._action_row())

            beliefs = self._satisfying_beliefs()

            async def read(entity: str) -> dict[str, float]:
                del entity
                return beliefs

            worker = LearningWorker(
                settings,
                decisions=decisions,
                contracts=ActionRegistry.from_directory(Path("actions")),
                beliefs=read,
                expectations=expectations.upsert,
            )
            applied = await worker.step()

            assert applied == 1, "承認が適用されていない"
            board = await expectations.board(target=PUMP)
            assert len(board) == 1
            assert board[0].status == "Active"
            assert board[0].action_type == vocab.ent.CreateEmergencyInspection
            # 期限は最も遅い効果（点検は 24 時間以内）
            assert board[0].deadline is not None
            await _cleanup_actions(connection)

    @pytest.mark.asyncio
    async def test_an_unwired_worker_records_the_failure(self, settings: Settings) -> None:
        """配線が無ければ失敗として残る（黙って成功にしない）。

        既定の配線では実行できない——業務システムを変える経路は運用者が明示する。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            decisions = DecisionQueue(connection, site=SITE)
            await decisions.enqueue(self._action_row(decision_id="a-unwired"))

            worker = LearningWorker(settings, decisions=decisions)
            assert await worker.step() == 0

            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT failed_at, error FROM learning_decisions WHERE decision_id = %s",
                    ("a-unwired",),
                )
                row = await cursor.fetchone()
            assert row is not None
            assert row[0] is not None, "失敗が記録されていない"
            assert "not wired" in str(row[1])
            await _cleanup_actions(connection)

    @pytest.mark.asyncio
    async def test_the_same_approval_executes_once(self, settings: Settings) -> None:
        """同じ承認を 2 回積んでも 1 回しか実行されない。

        **アクションの二重実行はこの基盤で最悪の失敗である**（業務システムを 2 回変える）。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            decisions = DecisionQueue(connection, site=SITE)
            expectations = ExpectationStore(connection, site=SITE)
            assert await decisions.enqueue(self._action_row(decision_id="a-dup")) is True
            assert await decisions.enqueue(self._action_row(decision_id="a-dup")) is False

            beliefs = self._satisfying_beliefs()

            async def read(entity: str) -> dict[str, float]:
                del entity
                return beliefs

            worker = LearningWorker(
                settings,
                decisions=decisions,
                contracts=ActionRegistry.from_directory(Path("actions")),
                beliefs=read,
                expectations=expectations.upsert,
            )
            await worker.step()
            await worker.step()

            assert len(await expectations.board(target=PUMP)) == 1
            await _cleanup_actions(connection)


async def _cleanup_actions(connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
    """アクションの判断と期待を落とす。"""
    async with connection.cursor() as cursor:
        await cursor.execute("DELETE FROM learning_decisions WHERE site = %s", (SITE,))
        await cursor.execute("DELETE FROM expectations WHERE site = %s", (SITE,))
    await connection.commit()
