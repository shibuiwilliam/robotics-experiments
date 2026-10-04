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

from typing import TYPE_CHECKING

import psycopg
import pytest
from fastapi.testclient import TestClient

from sbp.api.factory import build_app
from sbp.core.config import Settings
from sbp.core.metrics import MetricSample, metric
from sbp.core.time import now
from sbp.knowledge import MetricStore

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
