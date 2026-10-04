"""不一致の作業項目表（M8-12、ADR-0013）。

**ここでしか確かめられない。** `DiscrepancyStore` は SQL（終端状態からの後退を弾く UPSERT、
部分索引を使うキュー、GIN 索引の配列検索）そのものである。

重点:

1. **終端状態から非終端へ戻せない** — 解決済みの不一致がキューに戻ったように見えない。
2. **キューは深刻度降順** — UI が最初に見る順序。
3. **終端状態を既定で除く** — キューの上位が履歴で埋まらない。
4. **抑制したものも残る** — 抑制の妥当性を人が棚卸しする（OPS-03）。
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

from sbp.core.config import Settings
from sbp.core.errors import KnowledgeError
from sbp.core.time import Interval, now
from sbp.knowledge import DiscrepancyStore
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    DiscrepancyStatus,
    LearningSignals,
    Resolution,
    ResolutionOutcome,
    Severity,
    Suppression,
    from_record,
    to_record,
)

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SITE = "siteDiscrepancyTest"
PUMP = "https://sbp.example/site/siteA/asset/P-101"
LINE = "https://sbp.example/site/siteA/asset/LINE-1"
ROLE = "https://sbp.example/role/maintenance-planner"


def _discrepancy(
    *,
    score: float = 0.5,
    kind: DiscrepancyKind = DiscrepancyKind.STATE,
    subjects: tuple[str, ...] = (PUMP,),
) -> Discrepancy:
    start = now().replace(microsecond=0) - timedelta(hours=1)
    return Discrepancy(
        kind=kind,
        subjects=subjects,
        assertions=("urn:sbp:assertion:1",),
        evidence=("urn:sbp:record:A",),
        detected_at=start,
        interval=Interval(start, start + timedelta(minutes=10)),
        severity=Severity(score=score, value_at_stake=1000.0, confidence=0.9, persistence_s=600.0),
        detector="kind2",
        ontology_version="0.5.0",
    )


async def _cleanup(connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
    async with connection.cursor() as cursor:
        await cursor.execute("DELETE FROM discrepancies WHERE site = %s", (SITE,))
    await connection.commit()


class TestWriteAndRead:
    """書く・読む。"""

    @pytest.mark.asyncio
    async def test_a_written_discrepancy_round_trips(self, settings: Settings) -> None:
        """書いた不一致が本体ごと戻る（証拠 IRI も保たれる）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)
            original = _discrepancy()
            await store.upsert(to_record(original))

            record = await store.get(original.discrepancy_id)
            assert record is not None
            assert from_record(record) == original
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_a_missing_discrepancy_returns_none(self, settings: Settings) -> None:
        """無い識別子は ``None``（例外にしない）。

        learning-worker は「本体が無いので最小の不一致で代替する」判断ができる必要がある。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)

            assert await store.get(uuid4()) is None
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_the_queue_is_ordered_by_severity(self, settings: Settings) -> None:
        """キューは深刻度の高い順（UI が最初に見る順序）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)
            for score in (0.2, 0.9, 0.5):
                await store.upsert(to_record(_discrepancy(score=score)))

            scores = [record.severity_score for record in await store.queue()]
            assert scores == [0.9, 0.5, 0.2]
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_the_queue_can_filter_by_kind(self, settings: Settings) -> None:
        """五類型で絞れる（「どの類型が増えたか」を見る）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)
            await store.upsert(to_record(_discrepancy(kind=DiscrepancyKind.STATE)))
            await store.upsert(to_record(_discrepancy(kind=DiscrepancyKind.EXISTENCE)))

            assert [r.kind for r in await store.queue(kind=1)] == [1]
            assert [r.kind for r in await store.queue(kind=2)] == [2]
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_other_sites_are_not_returned(self, settings: Settings) -> None:
        """他拠点の不一致は返らない（拠点間の情報分離）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            await DiscrepancyStore(connection, site=SITE).upsert(to_record(_discrepancy()))

            other = DiscrepancyStore(connection, site=f"{SITE}-other")
            assert await other.queue() == []
            await _cleanup(connection)


class TestStatusTransitions:
    """状態の進行と後退。"""

    @pytest.mark.asyncio
    async def test_a_status_update_is_recorded(self, settings: Settings) -> None:
        """状態を進めた結果が反映される。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)
            original = _discrepancy()
            await store.upsert(to_record(original))

            triaged = original.transition(DiscrepancyStatus.TRIAGED, at=now())
            await store.upsert(to_record(triaged))

            record = await store.get(original.discrepancy_id)
            assert record is not None
            assert record.status == "Triaged"
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_a_terminal_discrepancy_leaves_the_queue(self, settings: Settings) -> None:
        """終端状態はキューから外れる（履歴でキューが埋まらない）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)
            original = _discrepancy()
            suppressed = original.transition(
                DiscrepancyStatus.SUPPRESSED,
                at=now(),
                suppression=Suppression(
                    bias_kind="shift_boundary",
                    reliability_key="ent:MesSource|ent:OperationLog",
                    reason="シフト境界の丸めで説明できる",
                    suppressed_at=now(),
                ),
            )
            await store.upsert(to_record(suppressed))

            assert await store.queue() == []
            # 履歴としては残る（抑制の妥当性を人が棚卸しする。OPS-03）
            assert len(await store.queue(include_terminal=True)) == 1
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_the_lifecycle_cannot_move_backwards(self, settings: Settings) -> None:
        """進んだ状態を古い状態で上書きできない。

        複数の経路（照合ワーカーと learning-worker）が同じ行を触るので、片方が古い状態で
        上書きする事故が起こりうる。**終端だけを守っても足りない**——`Resolved` の行を
        `Open` で上書きできてしまう（所見 80）。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)
            original = _discrepancy()
            triaged = original.transition(DiscrepancyStatus.TRIAGED, at=now())
            await store.upsert(to_record(triaged))

            # 1 段だけ戻す（終端ではないので、終端チェックだけでは通ってしまう）
            with pytest.raises(KnowledgeError, match="backwards"):
                await store.upsert(to_record(original))

            record = await store.get(original.discrepancy_id)
            assert record is not None
            assert record.status == "Triaged", "後退が通っている"
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_the_same_rank_can_be_updated(self, settings: Settings) -> None:
        """同じ位置なら内容の更新として通る（深刻度の再計算など）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)
            original = _discrepancy(score=0.3)
            await store.upsert(to_record(original))

            heavier = original.model_copy(
                update={
                    "severity": Severity(
                        score=0.8, value_at_stake=5000.0, confidence=0.9, persistence_s=900.0
                    )
                }
            )
            await store.upsert(to_record(heavier))

            record = await store.get(original.discrepancy_id)
            assert record is not None
            assert record.severity_score == 0.8
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_a_terminal_discrepancy_cannot_regress(self, settings: Settings) -> None:
        """終端状態（``Learned``）から戻せない。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)
            original = _discrepancy()
            resolution = Resolution(
                outcome=ResolutionOutcome.RECORD_CORRECT,
                note="記録が正しい",
                resolved_by=ROLE,
                resolved_at=now(),
            )
            resolved = (
                original.transition(DiscrepancyStatus.TRIAGED, at=now())
                .transition(DiscrepancyStatus.ASSIGNED, at=now(), assignee_role=ROLE)
                .transition(DiscrepancyStatus.RESOLVED, at=now(), resolution=resolution)
            )
            learned = resolved.transition(
                DiscrepancyStatus.LEARNED,
                at=now(),
                learning=LearningSignals(labels_emitted=("urn:sbp:bag:1",)),
            )
            await store.upsert(to_record(learned))

            with pytest.raises(KnowledgeError, match="backwards"):
                await store.upsert(to_record(original))
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_counts_by_status(self, settings: Settings) -> None:
        """状態ごとの件数が引ける（運用の棚卸しと指標）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)
            await store.upsert(to_record(_discrepancy(score=0.1)))
            second = _discrepancy(score=0.2)
            await store.upsert(to_record(second.transition(DiscrepancyStatus.TRIAGED, at=now())))

            counts = await store.count_by_status()
            assert counts == {"Open": 1, "Triaged": 1}
            await _cleanup(connection)


class TestMultiSubject:
    """複数実体の不一致（類型 4）。"""

    @pytest.mark.asyncio
    async def test_all_subjects_are_stored(self, settings: Settings) -> None:
        """主体が複数ある不一致も全部残る。

        起票イベントは 1 つしか主体を選べない（ADR-0010）が、**表は全部持つ**——
        イベントはパーティションキーの制約で、本体は制約されない。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = DiscrepancyStore(connection, site=SITE)
            original = _discrepancy(kind=DiscrepancyKind.IDENTITY, subjects=(PUMP, LINE))
            await store.upsert(to_record(original))

            record = await store.get(original.discrepancy_id)
            assert record is not None
            assert set(record.subjects) == {PUMP, LINE}
            await _cleanup(connection)
