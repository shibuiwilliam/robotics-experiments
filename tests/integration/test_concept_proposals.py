"""概念提案の作業項目表（M8-19、ADR-0015）。

**ここでしか確かめられない。** `ConceptProposalStore` は SQL（後退を弾く UPSERT、支持の多い順の
並べ替え、`jsonb_set` で本体を読まずに状態を進める更新）そのものである。

重点:

1. **支持の多い順**（偶然のクラスタと区別できない提案を先頭に置かない。設計書 §11.2）。
2. **採否が入った提案を `proposed` に戻せない**（判断済みが待ち行列に戻らない）。
3. **本体を読まずに採否を進められる** — 読み戻すと重心を欠いた提案を書き直すことになる。
4. **採否が入ると待ち行列から外れる**（同じ提案が永久に画面に残らない）。
"""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import psycopg
import pytest

from sbp.core.config import Settings
from sbp.core.errors import KnowledgeError
from sbp.core.time import now
from sbp.knowledge import ConceptProposalStore
from sbp.reasoning.concept_store import from_record, to_record
from sbp.reasoning.discovery import (
    ConceptProposal,
    DiscoverySignal,
    DiscoveryTrigger,
    LatentCluster,
    ProposalStatus,
)

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SITE = "siteConceptTest"
PUMP = "https://sbp.example/site/siteA/asset/P-101"


def _proposal(*, name: str = "VibrationAnomaly", size: int = 548) -> ConceptProposal:
    cluster = LatentCluster(
        cluster_id=7,
        member_indices=tuple(range(size)),
        centroid=np.zeros(16, dtype=np.float64),
        mean_probe_activation=0.0,
        density=1.5,
    )
    return ConceptProposal(
        name=name,
        definition="既存の Faulted では説明できない振動の増大が停止の前段に現れる",
        superclass="Fluent",
        rejected_alternatives=("Faulted の閾値調整",),
        signals=(
            DiscoverySignal(
                trigger=DiscoveryTrigger.LOW_COVERAGE_CLUSTER,
                entities=(PUMP,),
                strength=0.873,
                evidence=("cluster:7",),
                note="既知プローブが反応しない高密度クラスタ",
                cluster=cluster,
            ),
        ),
        proposed_at=now().replace(microsecond=0),
    )


async def _cleanup(connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
    async with connection.cursor() as cursor:
        await cursor.execute("DELETE FROM concept_proposals WHERE site = %s", (SITE,))
    await connection.commit()


class TestWriteAndRead:
    """書く・読む。"""

    @pytest.mark.asyncio
    async def test_a_written_proposal_round_trips(self, settings: Settings) -> None:
        """書いた提案が戻る（重心以外）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ConceptProposalStore(connection, site=SITE)
            original = _proposal()
            await store.upsert(to_record(original))

            record = await store.get(original.proposal_id)
            assert record is not None
            assert record.support_count == 548
            restored = from_record(record)
            assert restored.name == original.name
            assert restored.definition == original.definition
            # 重心は復元しない（ADR-0015）
            assert restored.signals[0].cluster is None
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_the_queue_is_ordered_by_support(self, settings: Settings) -> None:
        """支持の多い順に返る（設計書 §11.2）。

        偶然のクラスタと区別できない提案を先頭に置くと、人の判断の時間が弱い提案に使われる。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ConceptProposalStore(connection, site=SITE)
            for name, size in (("Weak", 3), ("Strong", 548), ("Middle", 120)):
                await store.upsert(to_record(_proposal(name=name, size=size)))

            counts = [record.support_count for record in await store.queue()]
            assert counts == sorted(counts, reverse=True)
            assert counts[0] == 548
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_other_sites_are_not_returned(self, settings: Settings) -> None:
        """他拠点の提案は返らない。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            await ConceptProposalStore(connection, site=SITE).upsert(to_record(_proposal()))

            other = ConceptProposalStore(connection, site=f"{SITE}-other")
            assert await other.queue() == []
            await _cleanup(connection)


class TestCuration:
    """採否。"""

    @pytest.mark.asyncio
    async def test_curating_without_reading_the_body(self, settings: Settings) -> None:
        """本体を読まずに採否を進められる。

        **読み戻すと重心を欠いた提案を書き直すことになる**（ADR-0015 の往復の非対称性）。
        `curate` は `jsonb_set` で状態と理由だけを差し替える。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ConceptProposalStore(connection, site=SITE)
            original = _proposal()
            await store.upsert(to_record(original))

            updated = await store.curate(
                original.proposal_id,
                status=ProposalStatus.REJECTED.value,
                rank=ProposalStatus.REJECTED.rank,
                note="既存概念の閾値調整で足りる",
            )

            assert updated is True
            record = await store.get(original.proposal_id)
            assert record is not None
            assert record.status == "rejected"
            # 本体も揃っている（状態と理由だけが差し替わり、signals が残る）
            restored = from_record(record)
            assert restored.status is ProposalStatus.REJECTED
            assert restored.curation_note == "既存概念の閾値調整で足りる"
            assert len(restored.signals) == 1
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_curating_moves_updated_at_forward(self, settings: Settings) -> None:
        """却下の冷却の起点（ADR-0019）。採否を入れると ``updated_at`` が進む。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ConceptProposalStore(connection, site=SITE)
            original = _proposal()
            await store.upsert(to_record(original))
            before = await store.get(original.proposal_id)
            assert before is not None
            assert before.updated_at is not None
            await store.curate(
                original.proposal_id,
                status=ProposalStatus.REJECTED.value,
                rank=ProposalStatus.REJECTED.rank,
                note="既存概念で足りる",
            )
            after = await store.get(original.proposal_id)
            assert after is not None
            assert after.updated_at is not None
            assert after.updated_at >= before.updated_at
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_a_curated_proposal_leaves_the_queue(self, settings: Settings) -> None:
        """採否が入ると待ち行列から外れる（履歴には残る）。

        **初版は記録するだけで、同じ提案が永久に画面に残っていた。**
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ConceptProposalStore(connection, site=SITE)
            original = _proposal()
            await store.upsert(to_record(original))
            await store.curate(
                original.proposal_id,
                status=ProposalStatus.ACCEPTED.value,
                rank=ProposalStatus.ACCEPTED.rank,
                note="新概念として採用する",
            )

            assert await store.queue() == []
            assert len(await store.queue(include_curated=True)) == 1
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_curating_twice_does_not_overwrite(self, settings: Settings) -> None:
        """既に採否が入った提案は更新しない（``False`` を返す）。

        2 人が別の判断を入れたとき、後の判断が黙って勝つのを避ける。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ConceptProposalStore(connection, site=SITE)
            original = _proposal()
            await store.upsert(to_record(original))
            await store.curate(
                original.proposal_id,
                status=ProposalStatus.ACCEPTED.value,
                rank=ProposalStatus.ACCEPTED.rank,
                note="採用",
            )

            second = await store.curate(
                original.proposal_id,
                status=ProposalStatus.REJECTED.value,
                rank=ProposalStatus.REJECTED.rank,
                note="却下",
            )

            assert second is False
            record = await store.get(original.proposal_id)
            assert record is not None
            assert record.status == "accepted"
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_a_missing_proposal_reports_false(self, settings: Settings) -> None:
        """存在しない提案の採否は ``False``（例外にしない）。

        人の判断は失わせず、「提案が見つからなかった」ことを呼び出し側に返す。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ConceptProposalStore(connection, site=SITE)

            assert (
                await store.curate(
                    uuid4(),
                    status=ProposalStatus.ACCEPTED.value,
                    rank=ProposalStatus.ACCEPTED.rank,
                    note="採用",
                )
                is False
            )

    @pytest.mark.asyncio
    async def test_upsert_refuses_to_reopen(self, settings: Settings) -> None:
        """判断済みの提案を ``proposed`` に戻す書き込みを拒否する。

        発見ループが同じ提案を再度書こうとしたときに、却下した判断が消えるのを防ぐ。
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ConceptProposalStore(connection, site=SITE)
            original = _proposal()
            curated = original.curate(ProposalStatus.REJECTED, note="既存概念で足りる")
            await store.upsert(to_record(curated))

            with pytest.raises(KnowledgeError, match="already been curated"):
                await store.upsert(to_record(original))
            await _cleanup(connection)

    @pytest.mark.asyncio
    async def test_counts_by_status(self, settings: Settings) -> None:
        """採否ごとの件数が引ける（運用の棚卸しと指標）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            store = ConceptProposalStore(connection, site=SITE)
            await store.upsert(to_record(_proposal(name="A")))
            await store.upsert(
                to_record(_proposal(name="B").curate(ProposalStatus.ACCEPTED, note="採用"))
            )

            assert await store.count_by_status() == {"proposed": 1, "accepted": 1}
            await _cleanup(connection)
