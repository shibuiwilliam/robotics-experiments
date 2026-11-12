"""三源の並置を、イベントから API まで実サービス上で通す（M8-17-5、ADR-0020）。

外界（業務システムの主張・プローブの出力・計画）だけをテストが置き、belief-worker は production の
``build_worker``、API は production の ``build_app``:

    物理観測（bridge.physical）+ 業務の主張（integration.business）+ 計画（planned の文）
        → belief-worker（信念 + 寄与の投影）→ GraphQL ``beliefEvidence``

確かめること:

1. 観測が支配的な点・記録が支配的な点・計画が効いた点が、寄与として返る。
2. 三源は **様相では分かれない**（どちらも ``inferred`` の信念）が、寄与の源で分かれる（所見 84）。
3. 計画の区間が並置に出る。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic
from fastapi.testclient import TestClient
from services.belief_worker import build_worker as build_belief_worker

from sbp.api.factory import build_app
from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.events import (
    EventEnvelope,
    EventLayer,
    ProbeEvidence,
    Producer,
    RecordEvidence,
    ValidTime,
    topic_name,
)
from sbp.core.ids import new_event_id, site_iri
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.eventlog.producer import KafkaEventProducer
from sbp.knowledge import AssertionStore, HttpGraphStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sbp.core.config import Settings

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

DEADLINE_S = 30.0
RUNNING = str(vocab.ent.Running)
OPERATOR = "https://sbp.example/person/operator-e2e"


@pytest.fixture
def site(test_graph_suffix: str, assertion_store: AssertionStore) -> str:
    """テストごとの拠点（``assertion_store`` は後片付けのために要求する）。"""
    del assertion_store
    return f"{test_graph_suffix}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def topics(site: str, settings: Settings, kafka_available: None) -> Iterator[dict[str, str]]:
    del kafka_available
    names = {
        "business": topic_name(site, EventLayer.INTEGRATION, "business"),
        "physical": topic_name(site, EventLayer.BRIDGE, "physical"),
    }
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
    for future in admin.create_topics(
        [NewTopic(name, num_partitions=1, replication_factor=1) for name in names.values()]
    ).values():
        future.result(timeout=20)
    yield names
    for future in admin.delete_topics(list(names.values())).values():
        with suppress(Exception):
            future.result(timeout=20)


@pytest.fixture
async def cleanup(settings: Settings, site: str, postgres_available: None) -> AsyncIterator[None]:
    del postgres_available
    yield
    async with await psycopg.AsyncConnection.connect(settings.pg_dsn, autocommit=True) as conn:
        await conn.execute("DELETE FROM belief_contributions WHERE site = %s", (site,))  # type: ignore[arg-type]


def _probe(entity: str, *, at: Any, p: float) -> EventEnvelope:
    return EventEnvelope(
        event_id=new_event_id(),
        type=f"{vocab.core.PhysicalEvent}/{RUNNING}",
        subject=entity,
        valid_time=ValidTime(start=at, uncertainty_s=5.0),
        tx_time=at,
        modality=Modality.INFERRED,
        belief=BooleanBelief(p=p),
        evidence=(ProbeEvidence(probe_id="https://sbp.example/probe/Running/1.0.0"),),
        producer=Producer(agent="bridge@e2e", version="0.1.0"),
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


def _claim(entity: str, *, at: Any, p: float) -> EventEnvelope:
    return EventEnvelope(
        event_id=new_event_id(),
        type=f"{vocab.core.BusinessEvent}/{RUNNING}",
        subject=entity,
        valid_time=ValidTime(start=at, uncertainty_s=60.0),
        tx_time=at,
        modality=Modality.OBSERVED,
        belief=BooleanBelief(p=p),
        evidence=(
            RecordEvidence(
                record="https://sbp.example/record/r-e2e", source=str(vocab.ent.MesSource)
            ),
        ),
        producer=Producer(agent="integration@e2e", version="0.1.0"),
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


class TestEvidenceFromEventsToApi:
    @pytest.mark.usefixtures("cleanup")
    async def test_each_source_shows_up_where_it_dominated(
        self,
        settings: Settings,
        site: str,
        topics: dict[str, str],
        graph_store: HttpGraphStore,
    ) -> None:
        run_settings = settings.model_copy(update={"site_id": site, "env": "dev"})
        entity = site_iri(site, "asset", "P-101")
        base = now().replace(microsecond=0) - timedelta(minutes=30)

        # 外界: 計画（planned の文）、観測の出力、業務の主張
        AssertionStore(graph_store, site_id=site).assert_(
            Assertion(
                subject=entity,
                predicate=RUNNING,
                object=True,
                valid_from=base - timedelta(hours=1),
                valid_to=base + timedelta(hours=2),
                tx_from=base,
                modality=Modality.PLANNED,
                belief=BooleanBelief(p=0.9),
                provenance="urn:sbp:prov:plan/e2e",
                ontology_version=vocab.ONTOLOGY_VERSION,
            )
        )
        async with KafkaEventProducer(run_settings) as producer:
            await producer.send(topics["physical"], _probe(entity, at=base, p=0.97))
            await producer.send(
                topics["business"], _claim(entity, at=base + timedelta(minutes=5), p=0.05)
            )

        worker, consumer = await build_belief_worker(run_settings, site_id=site)
        try:
            deadline = time.monotonic() + DEADLINE_S
            while worker.applied_total < 2 and time.monotonic() < deadline:
                await worker.step()
            assert worker.applied_total >= 2
        finally:
            await consumer.stop()

        start = (base - timedelta(minutes=5)).isoformat()
        end = (base + timedelta(minutes=30)).isoformat()

        def ask() -> dict[str, Any]:
            with TestClient(build_app(run_settings)) as client:
                response = client.post(
                    "/graphql",
                    json={
                        "query": f"""{{ beliefEvidence(entity: "{entity}", fluent: "{RUNNING}",
                            start: "{start}", end: "{end}") {{
                          points {{ at probability origin
                            contribution {{ observed record plan dominant }} }}
                          planned {{ adherence }} }} }}"""
                    },
                    headers={"X-SBP-Principal": OPERATOR},
                )
            return response.json()

        payload = await asyncio.to_thread(ask)
        assert not payload.get("errors"), payload.get("errors")
        data = payload["data"]["beliefEvidence"]
        points = data["points"]
        assert len(points) == 2
        assert all(point["origin"] == "update" for point in points)
        first, second = points[0]["contribution"], points[1]["contribution"]
        assert first["observed"] > 0.0, "観測が事後を動かした"
        assert first["plan"] > 0.0, "初期状態は計画の遵守率が事前になる（計画が効いた点）"
        assert second["record"] > 0.0, "記録の主張が事後を動かした"
        assert second["dominant"] == "record"
        assert second["observed"] == 0.0
        # 記録は「稼働していない」と主張し（p = 0.05）、事後は観測だけのときより下がる
        assert points[1]["probability"] < points[0]["probability"]
        assert data["planned"] == [{"adherence": pytest.approx(0.9)}]
