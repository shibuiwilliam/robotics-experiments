"""API の実配線を実物のサービス上で起動する（M8-13）。

**ここでしか確かめられない。** 単体テストは `create_app` に偽の依存を渡すので、
「実物の Oxigraph と Postgres に繋いで起動できるか」は一度も検査されていなかった
（`docs/PROGRESS.md` 所見 68）。

重点:

1. **lifespan が Postgres を開く** — `/metrics` が 503 でなく 200 を返す。
2. **指標が表 → exposition まで通る** — Prometheus が取り込める形になっている。
3. **要求者を名乗らなければ信念を返さない**（既定は拒否）。
4. **SPARQL プロキシが更新操作を拒否する** — 実物のストアに繋いでも二時制 API を迂回できない。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient

from sbp.api.factory import build_app
from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.config import Settings
from sbp.core.metrics import MetricSample, metric
from sbp.core.modality import Modality
from sbp.core.time import Interval, now
from sbp.knowledge import (
    AssertionStore,
    ConceptProposalStore,
    ContributionRecord,
    ContributionStore,
    DiscrepancyStore,
    ExpectationStore,
    HttpGraphStore,
    MetricStore,
)
from sbp.reasoning.concept_store import to_record as concept_to_record
from sbp.reasoning.discovery import (
    ConceptProposal,
    DiscoverySignal,
    DiscoveryTrigger,
    LatentCluster,
)
from sbp.reasoning.expectation import Expectation, ExpectationStatus, ExpectedEffect
from sbp.reasoning.expectation_store import to_record as expectation_to_record
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    Severity,
    to_record,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

OPERATOR = "https://sbp.example/person/operator-e2e"


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """実物の依存で組んだ API（lifespan を通す）。"""
    with TestClient(build_app(settings)) as started:
        yield started


class TestStartup:
    """起動と疎通。"""

    def test_the_app_starts_against_the_real_stack(self, client: TestClient) -> None:
        """実物の Oxigraph に繋いで起動できる。"""
        response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["ontology_version"]
        assert body["store_reachable"] is True

    def test_metrics_are_served_not_503(self, client: TestClient) -> None:
        """``/metrics`` が 200 を返す（``metric_reader`` が配線されている）。

        503 のままだと Prometheus の取り込み先が無い（M8-06 の前提）。
        """
        response = client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")


class TestMetricsPath:
    """表 → エクスポート。"""

    @pytest.mark.asyncio
    async def test_a_written_metric_appears_in_the_exposition(
        self, settings: Settings, client: TestClient
    ) -> None:
        """表に書いた指標が ``/metrics`` に出る（Prometheus が取り込める形）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            await MetricStore(connection, site=settings.site_id).write(
                [
                    MetricSample(
                        name=metric("probe_auroc"),
                        value=0.91,
                        at=now().replace(microsecond=0),
                        labels={"probe": "ApiFactoryE2E"},
                    )
                ]
            )
            await connection.commit()

        body = client.get("/metrics").text

        assert "# TYPE sbp_grounding_probe_auroc_ratio gauge" in body
        assert 'probe="ApiFactoryE2E"' in body

        # 後片付け（この表は投影なので消してよい。信念ではない）
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM metrics WHERE labels->>'probe' = %s", ("ApiFactoryE2E",)
                )
            await connection.commit()


class TestAuthorizationDefault:
    """既定は拒否。"""

    def test_without_a_principal_beliefs_are_refused(self, client: TestClient) -> None:
        """要求者を名乗らなければ信念を返さない。"""
        response = client.post(
            "/graphql", json={"query": '{ beliefs(subject: "urn:sbp:x") { subject } }'}
        )

        assert response.status_code == 200
        assert response.json().get("errors"), "要求者なしで信念が返っている"

    def test_the_header_grants_nothing_outside_dev(self, client: TestClient) -> None:
        """``SBP_ENV`` が ``dev`` でなければ、ヘッダを付けても拒否される。

        統合テストの環境は ``SBP_ENV=test`` である。**開発用の経路が test / prod に漏れて
        いないこと**をここで固定する——漏れていたら、ヘッダを名乗るだけで信念が読める。
        """
        response = client.post(
            "/graphql",
            json={"query": '{ beliefs(subject: "urn:sbp:nothing-here") { subject } }'},
            headers={"X-SBP-Principal": OPERATOR},
        )

        assert response.status_code == 200
        assert response.json().get("errors"), "test 環境でヘッダだけで信念が読めている"

    def test_a_dev_principal_can_query_in_dev(self, settings: Settings) -> None:
        """``SBP_ENV=dev`` では開発用のヘッダで問い合わせが通る。"""
        dev_settings = settings.model_copy(update={"env": "dev"})
        with TestClient(build_app(dev_settings)) as dev_client:
            response = dev_client.post(
                "/graphql",
                json={"query": '{ beliefs(subject: "urn:sbp:nothing-here") { subject } }'},
                headers={"X-SBP-Principal": OPERATOR},
            )

        assert response.status_code == 200
        assert not response.json().get("errors"), response.json().get("errors")


class TestSparqlProxy:
    """実物のストアに繋いでも二時制 API を迂回できない。"""

    def test_a_read_query_is_proxied(self, client: TestClient) -> None:
        """読み取りクエリは通る。"""
        response = client.post("/sparql", json={"query": "SELECT * WHERE { ?s ?p ?o } LIMIT 1"})

        assert response.status_code == 200
        assert "results" in response.json()

    def test_an_update_is_refused(self, client: TestClient) -> None:
        """更新操作は拒否される（信念は二時制 API 経由でのみ書く）。

        403（Forbidden）で返る——400 ではない。クエリの構文が不正なのではなく、
        **やってはいけない操作**だからである。
        """
        response = client.post("/sparql", json={"query": "INSERT DATA { <urn:a> <urn:b> <urn:c> }"})

        assert response.status_code == 403


class TestDiscrepancyResource:
    """不一致キューの GraphQL 資源（M8-12、ADR-0013）。

    **UI が読む先がこれである。** M8-04 までは起票イベントしか無く、イベントに全体が入って
    いない（所見 62）ので UI は中身を読めなかった。
    """

    @pytest.fixture
    def seeded(self, settings: Settings) -> Iterator[str]:
        """不一致を 1 件書いてから消す。"""
        start = now().replace(microsecond=0) - timedelta(hours=1)
        discrepancy = Discrepancy(
            kind=DiscrepancyKind.STATE,
            subjects=("https://sbp.example/site/siteA/asset/P-E2E",),
            assertions=("urn:sbp:assertion:e2e",),
            evidence=("urn:sbp:record:e2e",),
            detected_at=start,
            interval=Interval(start, start + timedelta(minutes=10)),
            severity=Severity(
                score=0.77, value_at_stake=1000.0, confidence=0.9, persistence_s=600.0
            ),
            detector="kind2",
            ontology_version="0.5.0",
        )

        async def write() -> None:
            async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
                await DiscrepancyStore(connection, site=settings.site_id).upsert(
                    to_record(discrepancy)
                )
                await connection.commit()

        async def clean() -> None:
            async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "DELETE FROM discrepancies WHERE discrepancy_id = %s",
                        (str(discrepancy.discrepancy_id),),
                    )
                await connection.commit()

        asyncio.run(write())
        yield str(discrepancy.discrepancy_id)
        asyncio.run(clean())

    def test_the_queue_is_served_over_graphql(self, settings: Settings, seeded: str) -> None:
        """不一致キューが GraphQL で読める（証拠は IRI で返る）。"""
        dev_settings = settings.model_copy(update={"env": "dev"})
        with TestClient(build_app(dev_settings)) as client:
            response = client.post(
                "/graphql",
                json={
                    "query": """{ discrepancies(limit: 50) {
                        discrepancyId kind status subjects severity detector
                        assertions evidence ontologyVersion intervalStart intervalEnd
                    } }"""
                },
                headers={"X-SBP-Principal": OPERATOR},
            )

        assert response.status_code == 200
        payload = response.json()
        assert not payload.get("errors"), payload.get("errors")
        rows = payload["data"]["discrepancies"]
        found = [row for row in rows if row["discrepancyId"] == seeded]
        assert found, "書いた不一致がキューに出ていない"
        assert found[0]["kind"] == 2
        assert found[0]["status"] == "Open"
        assert found[0]["evidence"] == ["urn:sbp:record:e2e"]
        assert found[0]["ontologyVersion"] == "0.5.0"
        # 区間は証拠の三源並置の鍵（M8-17）。右開の終端も返る
        assert found[0]["intervalStart"] is not None
        assert found[0]["intervalEnd"] is not None

    def test_the_queue_requires_a_principal(self, settings: Settings, seeded: str) -> None:
        """要求者を名乗らなければ不一致を返さない（既定は拒否）。

        不一致は実体の状態についての判断であり、信念と同じ機密度を持つ。
        """
        del seeded
        with TestClient(build_app(settings)) as client:
            response = client.post(
                "/graphql", json={"query": "{ discrepancies { discrepancyId } }"}
            )

        assert response.status_code == 200
        assert response.json().get("errors"), "要求者なしで不一致が返っている"


class TestExpectationResource:
    """期待ボードの GraphQL 資源（M8-18）。

    **UI が読む先がこれである。** 以前は存在しないトピック（`central.reasoning.expectation`）
    を読んでおり、しかもイベントに全体が入らなかった（所見 85 / 86）。
    """

    @pytest.fixture
    def seeded_expectation(self, settings: Settings) -> Iterator[str]:
        """期待を 1 件書いてから消す。"""
        created = now().replace(microsecond=0)
        expectation = Expectation(
            action_event=uuid4(),
            action_type="https://sbp.example/ent#CreateEmergencyInspection",
            target="https://sbp.example/site/siteA/asset/P-E2E-EXP",
            expected_effects=(
                ExpectedEffect(
                    fluent="https://sbp.example/ent#Running",
                    entity="https://sbp.example/site/siteA/asset/P-E2E-EXP",
                    expected=0.9,
                    lower=0.8,
                    upper=1.0,
                    deadline=created + timedelta(hours=1),
                    origin="symbolic",
                ),
            ),
            status=ExpectationStatus.ACTIVE,
            created_at=created,
            ontology_version="0.5.0",
        )

        async def write() -> None:
            async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
                await ExpectationStore(connection, site=settings.site_id).upsert(
                    expectation_to_record(expectation)
                )
                await connection.commit()

        async def clean() -> None:
            async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "DELETE FROM expectations WHERE expectation_id = %s",
                        (str(expectation.expectation_id),),
                    )
                await connection.commit()

        asyncio.run(write())
        yield str(expectation.expectation_id)
        asyncio.run(clean())

    def test_the_board_is_served_over_graphql(
        self, settings: Settings, seeded_expectation: str
    ) -> None:
        """期待ボードが GraphQL で読める（2 面の判定が別々に返る）。"""
        dev_settings = settings.model_copy(update={"env": "dev"})
        with TestClient(build_app(dev_settings)) as client:
            response = client.post(
                "/graphql",
                json={
                    "query": """{ expectations(limit: 50) {
                        expectationId status actionType target createdAt deadline
                        symbolicVerdict rolloutVerdict method effectCount ontologyVersion
                    } }"""
                },
                headers={"X-SBP-Principal": OPERATOR},
            )

        assert response.status_code == 200
        payload = response.json()
        assert not payload.get("errors"), payload.get("errors")
        found = [
            row
            for row in payload["data"]["expectations"]
            if row["expectationId"] == seeded_expectation
        ]
        assert found, "書いた期待がボードに出ていない"
        assert found[0]["status"] == "Active"
        assert found[0]["effectCount"] == 1
        # 未検証なので両面が null（「当たり」として描かない）
        assert found[0]["symbolicVerdict"] is None
        assert found[0]["rolloutVerdict"] is None
        assert found[0]["method"] is None

    def test_the_board_requires_a_principal(
        self, settings: Settings, seeded_expectation: str
    ) -> None:
        """要求者を名乗らなければ期待を返さない（既定は拒否）。"""
        del seeded_expectation
        with TestClient(build_app(settings)) as client:
            response = client.post("/graphql", json={"query": "{ expectations { expectationId } }"})

        assert response.status_code == 200
        assert response.json().get("errors"), "要求者なしで期待が返っている"


class TestProposalResource:
    """概念 curation の GraphQL 資源（M8-19、ADR-0015）。

    **UI が読む先がこれである。** 以前は存在しないトピック（`central.reasoning.concept`）を
    読んでいて、型も backend と別物だった（所見 85）。
    """

    @pytest.fixture
    def seeded_proposal(self, settings: Settings) -> Iterator[str]:
        """提案を 1 件書いてから消す。"""
        cluster = LatentCluster(
            cluster_id=7,
            member_indices=tuple(range(548)),
            centroid=np.zeros(16, dtype=np.float64),
            mean_probe_activation=0.0,
            density=1.5,
        )
        proposal = ConceptProposal(
            name="VibrationAnomalyE2E",
            definition="既存の Faulted では説明できない振動の増大が停止の前段に現れる",
            superclass="Fluent",
            rejected_alternatives=("Faulted の閾値調整",),
            signals=(
                DiscoverySignal(
                    trigger=DiscoveryTrigger.LOW_COVERAGE_CLUSTER,
                    entities=("https://sbp.example/site/siteA/asset/P-E2E-CONCEPT",),
                    strength=0.873,
                    evidence=("cluster:7",),
                    note="既知プローブが反応しない高密度クラスタ",
                    cluster=cluster,
                ),
            ),
            proposed_at=now().replace(microsecond=0),
        )

        async def write() -> None:
            async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
                await ConceptProposalStore(connection, site=settings.site_id).upsert(
                    concept_to_record(proposal)
                )
                await connection.commit()

        async def clean() -> None:
            async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "DELETE FROM concept_proposals WHERE proposal_id = %s",
                        (str(proposal.proposal_id),),
                    )
                await connection.commit()

        asyncio.run(write())
        yield str(proposal.proposal_id)
        asyncio.run(clean())

    def test_the_queue_is_served_over_graphql(
        self, settings: Settings, seeded_proposal: str
    ) -> None:
        """curation の待ち行列が GraphQL で読める（判断材料つき）。"""
        dev_settings = settings.model_copy(update={"env": "dev"})
        with TestClient(build_app(dev_settings)) as client:
            response = client.post(
                "/graphql",
                json={
                    "query": """{ proposals(limit: 50) {
                        proposalId name definition superclass status rejectedAlternatives
                        supportCount coverage signalCount proposedAt curationNote
                    } }"""
                },
                headers={"X-SBP-Principal": OPERATOR},
            )

        assert response.status_code == 200
        payload = response.json()
        assert not payload.get("errors"), payload.get("errors")
        found = [
            row for row in payload["data"]["proposals"] if row["proposalId"] == seeded_proposal
        ]
        assert found, "書いた提案が待ち行列に出ていない"
        assert found[0]["status"] == "proposed"
        assert found[0]["name"] == "VibrationAnomalyE2E"
        # 判断材料（設計書 §11.2）が数値で返る
        assert found[0]["supportCount"] == 548
        assert found[0]["coverage"] == 0.0
        assert found[0]["signalCount"] == 1
        assert found[0]["rejectedAlternatives"] == ["Faulted の閾値調整"]
        assert found[0]["curationNote"] == ""

    def test_the_queue_requires_a_principal(self, settings: Settings, seeded_proposal: str) -> None:
        """要求者を名乗らなければ提案を返さない（既定は拒否）。"""
        del seeded_proposal
        with TestClient(build_app(settings)) as client:
            response = client.post("/graphql", json={"query": "{ proposals { proposalId } }"})

        assert response.status_code == 200
        assert response.json().get("errors"), "要求者なしで提案が返っている"


class TestEvidenceResource:
    """証拠の三源並置の GraphQL 資源（M8-17、ADR-0020）。

    実物の Oxigraph（信念・計画）と Postgres（寄与）を、実物の API が突き合わせる。
    """

    def test_beliefs_contributions_and_plans_come_back_side_by_side(
        self, settings: Settings, test_graph_suffix: str, assertion_store: AssertionStore
    ) -> None:
        del assertion_store  # 後片付け（テスト用グラフを消す）のために要求する
        site = f"{test_graph_suffix}-evidence"
        entity = f"https://sbp.example/site/{site}/asset/P-E2E"
        fluent = str(vocab.ent.Running)
        at = now().replace(microsecond=0) - timedelta(minutes=30)
        dev_settings = settings.model_copy(update={"env": "dev", "site_id": site})
        graph = AssertionStore(HttpGraphStore(dev_settings), site_id=site)
        belief = Assertion(
            subject=entity,
            predicate=fluent,
            object=True,
            valid_from=at,
            tx_from=at,
            modality=Modality.INFERRED,
            belief=BooleanBelief(p=0.8),
            provenance="urn:sbp:prov:belief-worker/evt-e2e",
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        plan = Assertion(
            subject=entity,
            predicate=fluent,
            object=True,
            valid_from=at - timedelta(hours=1),
            valid_to=at + timedelta(hours=1),
            tx_from=at,
            modality=Modality.PLANNED,
            belief=BooleanBelief(p=0.9),
            provenance="urn:sbp:prov:plan/e2e",
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        graph.assert_(belief)
        graph.assert_(plan)

        async def write() -> None:
            async with await psycopg.AsyncConnection.connect(dev_settings.pg_dsn) as connection:
                await ContributionStore(connection, site=site).write(
                    ContributionRecord(
                        assertion_id=belief.assertion_id,
                        entity=entity,
                        fluent=fluent,
                        at=at,
                        prior=0.5,
                        posterior=0.8,
                        observed=0.4,
                        record=2.1,
                        plan=0.3,
                        source_event_id="evt-e2e",
                    )
                )
                await connection.commit()

        async def clean() -> None:
            async with await psycopg.AsyncConnection.connect(dev_settings.pg_dsn) as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "DELETE FROM belief_contributions WHERE site = %s", (site,)
                    )
                await connection.commit()

        asyncio.run(write())
        try:
            start = (at - timedelta(minutes=5)).isoformat()
            end = (at + timedelta(minutes=5)).isoformat()
            with TestClient(build_app(dev_settings)) as client:
                response = client.post(
                    "/graphql",
                    json={
                        "query": f"""{{ beliefEvidence(entity: "{entity}", fluent: "{fluent}",
                            start: "{start}", end: "{end}") {{
                          points {{ assertionId probability origin
                            contribution {{ observed record plan dominant sourceEventId }} }}
                          planned {{ adherence }} }} }}"""
                    },
                    headers={"X-SBP-Principal": OPERATOR},
                )
        finally:
            asyncio.run(clean())

        payload = response.json()
        assert not payload.get("errors"), payload.get("errors")
        data = payload["data"]["beliefEvidence"]
        assert [p["assertionId"] for p in data["points"]] == [str(belief.assertion_id)]
        contribution = data["points"][0]["contribution"]
        assert contribution["dominant"] == "record"
        assert contribution["record"] == pytest.approx(2.1)
        assert contribution["sourceEventId"] == "evt-e2e"
        assert data["points"][0]["origin"] == "update"
        assert data["planned"] == [{"adherence": pytest.approx(0.9)}]
