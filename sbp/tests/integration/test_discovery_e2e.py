"""概念発見の周期実行を、production の配線で実 Postgres 上に通す（M8-09-5、ADR-0019）。

外界（人が解決した不一致）だけをテストが置き、learning-worker は production の ``build_worker``
（LLM は replay の fixture。実 LLM を使わない）。確かめること:

1. 反復して解決された不一致から、``concept_proposals`` に ``proposed`` が 1 件書かれる。
2. 2 周目は何も増えない（判断待ちの提案が指紋を抑える）。
3. **却下すると、冷却の間は再提案されない**（H-10）。冷却を過ぎると、同じ signal が再提案される。

fixture はプロンプト・スキーマ・既存フルーエント型の内容ハッシュが鍵なので、オントロジーに
フルーエントを足すと録り直しになる（``FixtureMissingError`` の文言に新しいハッシュが出る）。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from services.learning_worker import build_worker

from sbp.core.config import ReasoningSettings
from sbp.core.time import Interval, now
from sbp.knowledge import ConceptProposalStore, DiscrepancyStore
from sbp.reasoning.discovery import ProposalStatus
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    DiscrepancyStatus,
    Resolution,
    ResolutionOutcome,
    Severity,
    to_record,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sbp.core.config import Settings

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

PUMP = "https://sbp.example/site/siteA/asset/P-101"
ROLE = "https://sbp.example/role/planner"


@pytest.fixture
def site() -> str:
    return f"siteDiscovery{uuid.uuid4().hex[:6]}"


@pytest.fixture
async def connection(
    settings: Settings, site: str, postgres_available: None
) -> AsyncIterator[psycopg.AsyncConnection[tuple[Any, ...]]]:
    del postgres_available
    conn = await psycopg.AsyncConnection.connect(settings.pg_dsn, autocommit=True)
    yield conn
    for table in ("discrepancies", "concept_proposals", "learning_decisions"):
        await conn.execute(f"DELETE FROM {table} WHERE site = %s", (site,))  # type: ignore[arg-type]
    await conn.close()


def _resolved() -> Discrepancy:
    """人が「両方誤り」と解決した類型 2 の不一致。**この組が fixture のメッセージを決める**
    （件数・類型・解決結果。実体の IRI は説明に入らない）。"""
    detected = now() - timedelta(days=1)
    open_ = Discrepancy(
        kind=DiscrepancyKind.STATE,
        subjects=(PUMP,),
        detected_at=detected,
        interval=Interval(detected, detected + timedelta(minutes=10)),
        severity=Severity(score=0.5, value_at_stake=1.0, confidence=0.9, persistence_s=60.0),
        detector="kind2",
        ontology_version="0.6.0",
    )
    at = now()
    assigned = open_.transition(DiscrepancyStatus.TRIAGED, at=at).transition(
        DiscrepancyStatus.ASSIGNED, at=at, assignee_role=ROLE
    )
    return assigned.transition(
        DiscrepancyStatus.RESOLVED,
        at=at,
        resolution=Resolution(
            outcome=ResolutionOutcome.BOTH_WRONG,
            note="人が確認した",
            resolved_by=ROLE,
            resolved_at=at,
        ),
    )


class TestDiscoveryCycle:
    async def test_repeated_resolutions_become_one_proposal_and_a_rejection_cools_down(
        self,
        settings: Settings,
        site: str,
        connection: psycopg.AsyncConnection[tuple[Any, ...]],
    ) -> None:
        discrepancies = DiscrepancyStore(connection, site=site)
        for _ in range(3):
            await discrepancies.upsert(to_record(_resolved()))
        store = ConceptProposalStore(connection, site=site)

        run_settings = settings.model_copy(
            update={
                "site_id": site,
                "reasoning": ReasoningSettings(discovery_period_s=1e-6),
            }
        )
        worker, _decisions = await build_worker(run_settings, site_id=site)
        try:
            # 1. 提案が 1 件、proposed で書かれる（来歴付き）
            assert await worker.step() == 1
            rows = await store.queue(include_curated=True)
            assert [row.status for row in rows] == ["proposed"]
            assert rows[0].payload["llm_provenance"]["provider"] == "anthropic"

            # 2. 判断待ちの間は、2 周目に何も増えない（LLM も呼ばない）
            assert await worker.step() == 0
            assert len(await store.queue(include_curated=True)) == 1

            # 3. 却下すると、冷却の間は再提案されない
            proposal_id = rows[0].proposal_id
            assert await store.curate(
                proposal_id,
                status=ProposalStatus.REJECTED.value,
                rank=ProposalStatus.REJECTED.rank,
                note="既存の語彙の調整で足りる",
            )
            assert await worker.step() == 0, "冷却中は同じ signal を再提案しない"

            # 4. 却下から冷却（30 日）を過ぎると、同じ signal が再提案される
            await connection.execute(
                "UPDATE concept_proposals SET updated_at = now() - interval '40 days' "
                "WHERE proposal_id = %s",
                (str(proposal_id),),
            )
            assert await worker.step() == 1
            statuses = sorted(row.status for row in await store.queue(include_curated=True))
            assert statuses == ["proposed", "rejected"]
        finally:
            await worker.aclose()
