"""API 層の単体テスト（設計書 §10.1、M5-07）。

重点:

1. **GraphQL に mutation が無い** — 読み取りと副作用の経路を分け、承認フローを迂回させない。
2. **SPARQL プロキシが更新操作を通さない** — 二時制 API を迂回して信念を書けないようにする。
3. **時制・様相・確信度がフィールド引数である** — 引数なしでは答えられない問いを曖昧にしない。
4. **様相の既定は inferred** — 計画と観測と推定を混ぜて返さない。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from sbp.api import Permission, Principal, build_schema, create_app, is_read_only_sparql
from sbp.api.commands import IDENTITY_FIELDS, _to_row  # pyright: ignore[reportPrivateUsage]
from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.metrics import MetricSample, metric
from sbp.core.modality import Modality
from sbp.knowledge.assertions import AssertionRow

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"


class _FakeStore:
    """``AssertionStore`` の代わり（API 層が SPARQL を組み立てないことの確認にも使う）。"""

    def __init__(self, rows: list[AssertionRow] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> list[AssertionRow]:
        self.calls.append(kwargs)
        return self.rows


def _row(*, modality: Modality = Modality.INFERRED, confidence: float = 0.87) -> AssertionRow:
    return AssertionRow(
        assertion_id=uuid4(),
        subject=PUMP,
        predicate=vocab.ent.Running,
        object_="true",
        valid_from=T0,
        valid_to=None,
        tx_from=T0,
        tx_to=None,
        modality=modality,
        confidence=confidence,
        provenance="prov:bundle-1",
        ontology_version=vocab.ONTOLOGY_VERSION,
        graph="urn:sbp:g:inferred:siteA:0.5.0",
    )


@pytest.fixture
def store() -> _FakeStore:
    return _FakeStore([_row()])


def _site_a_reader(_request: Request) -> Principal:
    """siteA の稼働状態を読める担当者（ABAC、M7-06）。"""
    return Principal(
        subject="https://sbp.example/id/person/op1",
        permissions=(
            Permission(
                concept_types=frozenset({vocab.ent.Running}),
                modalities=frozenset({Modality.OBSERVED, Modality.INFERRED}),
                sites=frozenset({"siteA"}),
            ),
        ),
    )


@pytest.fixture
def client(store: _FakeStore) -> TestClient:
    app = create_app(
        settings=Settings(),
        assertion_store=store,  # type: ignore[arg-type]
        sparql_reader=lambda query: {"query": query, "results": []},
        command_handler=lambda name, payload: {"command": name, "echo": payload},
        principal_resolver=_site_a_reader,
    )
    return TestClient(app)


class TestSparqlGuard:
    @pytest.mark.parametrize(
        "query",
        [
            "SELECT ?s WHERE { ?s ?p ?o }",
            "ASK { ?s ?p ?o }",
            "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
            "DESCRIBE <https://sbp.example/x>",
            "SELECT * WHERE { ?s ?p ?o } # DELETE は書いていない",
        ],
    )
    def test_read_only_queries_pass(self, query: str) -> None:
        assert is_read_only_sparql(query)

    @pytest.mark.parametrize(
        "query",
        [
            "INSERT DATA { <a> <b> <c> }",
            "DELETE WHERE { ?s ?p ?o }",
            "CLEAR GRAPH <g>",
            "DROP GRAPH <g>",
            "LOAD <http://example.org/data>",
            "SELECT * WHERE { ?s ?p ?o } ; DELETE WHERE { ?s ?p ?o }",
        ],
    )
    def test_update_operations_are_blocked(self, query: str) -> None:
        """二時制 API を迂回して信念を書けると、不変条件 1・2 がどちらも破れる。"""
        assert not is_read_only_sparql(query)

    def test_the_endpoint_rejects_updates_with_403(self, client: TestClient) -> None:
        response = client.post("/sparql", json={"query": "INSERT DATA { <a> <b> <c> }"})
        assert response.status_code == 403
        assert "二時制 API" in response.json()["detail"]

    def test_the_endpoint_passes_reads_through(self, client: TestClient) -> None:
        response = client.post("/sparql", json={"query": "SELECT ?s WHERE { ?s ?p ?o }"})
        assert response.status_code == 200
        assert response.json()["query"].startswith("SELECT")

    def test_a_missing_query_is_a_400(self, client: TestClient) -> None:
        assert client.post("/sparql", json={}).status_code == 400


class TestGraphQLSurface:
    def test_there_is_no_mutation(self) -> None:
        """mutation を出すと、読み取りと同じ経路で副作用が出せる（§9.2 の迂回）。"""
        assert build_schema().mutation is None

    def test_the_temporal_and_modal_arguments_exist(self) -> None:
        """引数なしでは答えられない問いを曖昧にしない。"""
        printed = str(build_schema())
        assert "asOf: DateTime = null" in printed
        assert "validAt: DateTime = null" in printed
        assert "modality: Modality! = INFERRED" in printed
        assert "minConfidence: Float! = 0" in printed

    def test_every_modality_is_exposed(self) -> None:
        """様相の列挙が core と食い違うと、API の指定が静かに別の意味になる。"""
        printed = str(build_schema())
        for modality in Modality:
            assert modality.name in printed


class TestGraphQLQueries:
    def _ask(self, client: TestClient, query: str) -> dict[str, Any]:
        response = client.post("/graphql", json={"query": query})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "errors" not in payload, payload
        return payload["data"]

    def test_beliefs_are_returned_with_their_version(self, client: TestClient) -> None:
        data = self._ask(
            client,
            f'{{ beliefs(subject: "{PUMP}") '
            "{ subject predicate confidence modality ontologyVersion } }",
        )
        assert data["beliefs"][0]["subject"] == PUMP
        assert data["beliefs"][0]["modality"] == "INFERRED"
        assert data["beliefs"][0]["ontologyVersion"] == vocab.ONTOLOGY_VERSION

    def test_the_default_modality_is_inferred(self, client: TestClient, store: _FakeStore) -> None:
        """計画・観測・推定を混ぜて返すと、呼び出し側が区別せずに使う。"""
        self._ask(client, f'{{ beliefs(subject: "{PUMP}") {{ subject }} }}')
        assert store.calls[-1]["modality"] is Modality.INFERRED

    def test_the_arguments_reach_the_store(self, client: TestClient, store: _FakeStore) -> None:
        self._ask(
            client,
            f'{{ beliefs(subject: "{PUMP}", asOf: "2026-09-12T09:00:00Z", '
            'validAt: "2026-09-12T08:00:00Z", modality: PLANNED, minConfidence: 0.5) '
            "{ subject } }",
        )
        call = store.calls[-1]
        assert call["as_of"] == T0
        assert call["modality"] is Modality.PLANNED
        assert call["min_confidence"] == pytest.approx(0.5)

    def test_health_reports_the_ontology_version(self, client: TestClient) -> None:
        data = self._ask(client, "{ health { ontologyVersion storeReachable } }")
        assert data["health"]["ontologyVersion"] == vocab.ONTOLOGY_VERSION
        assert data["health"]["storeReachable"] is True


class TestAbacAtTheApi:
    """ABAC が API に効いている（M7-06）。"""

    def _errors(self, client: TestClient, query: str) -> list[dict[str, Any]]:
        response = client.post("/graphql", json={"query": query})
        return response.json().get("errors", [])

    def test_without_a_resolver_beliefs_are_denied(self, store: _FakeStore) -> None:
        """ABAC 未設定で信念を読めない（既定は拒否）。

        「開発中だから全部見せる」を残すと、拠点間の情報分離も人に関する遮蔽も効かないまま
        本番に出る。疎通（health）は答えてよい——信念を返さないので。
        """
        app = create_app(settings=Settings(), assertion_store=store)  # type: ignore[arg-type]
        client = TestClient(app)
        errors = self._errors(client, f'{{ beliefs(subject: "{PUMP}") {{ subject }} }}')
        assert errors
        assert "ABAC が設定されていない" in errors[0]["message"]
        assert (
            "health"
            in client.post("/graphql", json={"query": "{ health { storeReachable } }"}).json()[
                "data"
            ]
        )

    def test_rows_outside_the_permitted_site_are_filtered(self, store: _FakeStore) -> None:
        """許可されていない拠点の行は返さない（クエリの後で絞る）。"""
        store.rows = [_row()]
        app = create_app(
            settings=Settings(),
            assertion_store=store,  # type: ignore[arg-type]
            principal_resolver=lambda _request: Principal(
                subject="https://sbp.example/id/person/opB",
                permissions=(
                    Permission(
                        concept_types=frozenset({vocab.ent.Running}),
                        modalities=frozenset({Modality.INFERRED}),
                        sites=frozenset({"siteB"}),
                    ),
                ),
            ),
        )
        response = TestClient(app).post(
            "/graphql", json={"query": f'{{ beliefs(subject: "{PUMP}") {{ subject }} }}'}
        )
        assert response.json()["data"]["beliefs"] == []

    def test_permitted_rows_still_come_back(self, client: TestClient) -> None:
        """絞り込みが効きすぎて全部消えることがない（陰性対照）。"""
        response = client.post(
            "/graphql", json={"query": f'{{ beliefs(subject: "{PUMP}") {{ subject }} }}'}
        )
        assert len(response.json()["data"]["beliefs"]) == 1


class TestStreamAndCommands:
    def test_there_is_no_stream_endpoint(self, client: TestClient) -> None:
        """購読も投入も API に無い（ADR-0033。外からイベントを流し込ませない）。"""
        assert client.get("/stream/siteA.bridge.physical").status_code == 404
        assert client.post("/stream/siteA.bridge.physical", json={}).status_code == 404

    def test_commands_go_through_their_own_path(self, client: TestClient) -> None:
        response = client.post("/commands/execute_action", json={"action_type": "x"})
        assert response.status_code == 200
        assert response.json()["command"] == "execute_action"

    def test_a_failing_command_is_a_409(self, store: _FakeStore) -> None:
        def explode(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            del name, payload
            raise ValueError("前提が未達")

        app = create_app(
            settings=Settings(),
            assertion_store=store,  # type: ignore[arg-type]
            command_handler=explode,
            principal_resolver=_site_a_reader,
        )
        response = TestClient(app).post("/commands/execute_action", json={})
        assert response.status_code == 409
        assert "前提が未達" in response.json()["detail"]

    def test_unconfigured_endpoints_report_503(self, store: _FakeStore) -> None:
        """設定していない経路を「空の成功」で返さない。"""
        app = create_app(settings=Settings(), assertion_store=store)  # type: ignore[arg-type]
        client = TestClient(app)
        assert (
            client.post("/sparql", json={"query": "SELECT * WHERE {?s ?p ?o}"}).status_code == 503
        )
        assert client.post("/commands/x", json={}).status_code == 503


def _nobody(_request: Request) -> Principal | None:
    return None


def _an_agent(_request: Request) -> Principal | None:
    return Principal(subject="https://x/agent/a", is_agent=True)


class TestCommandIdentity:
    """コマンドの「誰が」は認証済みの要求者から決まる（H-25、ADR-0029）。"""

    OP = "https://sbp.example/id/person/op1"

    def _client(self, resolver: Any = _site_a_reader) -> tuple[TestClient, list[dict[str, Any]]]:
        seen: list[dict[str, Any]] = []

        def handler(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            seen.append({"name": name, **payload})
            return {"accepted": True}

        app = create_app(
            settings=Settings(),
            assertion_store=_FakeStore(),  # type: ignore[arg-type]
            command_handler=handler,
            principal_resolver=resolver,
        )
        return TestClient(app), seen

    @pytest.mark.parametrize(
        ("name", "fieldname"),
        [
            ("resolve_discrepancy", "resolved_by"),
            ("curate_concept", "decided_by"),
            ("execute_action", "approved_by"),
            ("answer_observation", "answered_by"),
            ("review_quarantine", "reviewed_by"),
        ],
    )
    def test_the_actor_field_is_filled_from_the_principal(self, name: str, fieldname: str) -> None:
        client, seen = self._client()
        assert client.post(f"/commands/{name}", json={"x": 1}).status_code == 200
        assert seen[0][fieldname] == self.OP

    @pytest.mark.parametrize(
        ("name", "fieldname"),
        [
            ("resolve_discrepancy", "resolved_by"),
            ("curate_concept", "decided_by"),
            ("execute_action", "approved_by"),
            ("answer_observation", "answered_by"),
            ("review_quarantine", "reviewed_by"),
        ],
    )
    def test_naming_someone_else_is_refused(self, name: str, fieldname: str) -> None:
        client, seen = self._client()
        response = client.post(f"/commands/{name}", json={fieldname: "https://x/person/boss"})
        assert response.status_code == 403
        assert seen == [], "ハンドラに届かない"

    def test_naming_oneself_is_accepted(self) -> None:
        client, seen = self._client()
        assert (
            client.post("/commands/answer_observation", json={"answered_by": self.OP}).status_code
            == 200
        )
        assert seen[0]["answered_by"] == self.OP

    def test_without_a_resolvable_principal_nothing_is_accepted(self) -> None:
        client, seen = self._client(resolver=_nobody)
        assert client.post("/commands/answer_observation", json={}).status_code == 401
        no_resolver, seen2 = self._client(resolver=None)
        assert no_resolver.post("/commands/answer_observation", json={}).status_code == 401
        assert seen == seen2 == []

    def test_every_known_command_has_an_actor_field(self) -> None:
        """`_to_row` が知るコマンドと `IDENTITY_FIELDS` が一致する（足し忘れで自称が通らない）。"""
        for name in IDENTITY_FIELDS:
            with pytest.raises(ValueError) as caught:
                _to_row(name, {})  # pyright: ignore[reportPrivateUsage]
            assert "unknown command" not in str(caught.value), name

    def test_an_unknown_command_is_refused_even_for_a_known_principal(self) -> None:
        client, seen = self._client()
        assert client.post("/commands/brand_new", json={"x": 1}).status_code == 409
        assert seen == []

    @pytest.mark.parametrize("claimed", ["", [], ["x"], 0, "  "])
    def test_ill_typed_claims_are_refused(self, claimed: Any) -> None:
        client, seen = self._client()
        response = client.post("/commands/answer_observation", json={"answered_by": claimed})
        assert response.status_code == 403
        assert seen == []

    def test_a_null_claim_is_treated_as_absent(self) -> None:
        client, seen = self._client()
        client.post("/commands/answer_observation", json={"answered_by": None})
        assert seen[0]["answered_by"] == self.OP

    def test_an_alias_field_does_not_reach_the_handler_as_the_actor(self) -> None:
        """別名は主体の欄ではない。ハンドラ（`_to_row`）が `extra=forbid` で弾く。"""
        client, seen = self._client()
        client.post("/commands/answer_observation", json={"answeredBy": "https://x/boss"})
        assert seen[0]["answered_by"] == self.OP

    def test_unauthenticated_requests_are_refused_before_the_body_is_read(self) -> None:
        client, _seen = self._client(resolver=_nobody)
        response = client.post(
            "/commands/answer_observation", content=b"not json", headers={"content-type": "x/y"}
        )
        assert response.status_code == 401

    def test_a_malformed_body_is_a_400(self) -> None:
        client, _seen = self._client()
        response = client.post(
            "/commands/answer_observation", content=b"not json", headers={"content-type": "x/y"}
        )
        assert response.status_code == 400
        assert client.post("/commands/answer_observation", json=[1]).status_code == 400

    def test_an_agent_cannot_make_a_human_decision(self) -> None:
        client, seen = self._client(resolver=_an_agent)
        assert client.post("/commands/execute_action", json={}).status_code == 403
        assert seen == []


class TestMetricsEndpoint:
    """Prometheus の exposition（M8-06）。

    重点:

    1. **未配線は 503** — 空の exposition を返すと「スクレイプ成功、系列 0 件」と読まれ、
       指標が止まっていることと配線していないことが区別できない（所見 32）。
    2. **媒体型を付ける** — 付けないと取り込み側が推測する。
    3. **HELP / TYPE が出る** — ダッシュボードを作る人が系列の意味を推測しない。
    """

    @staticmethod
    def _client(samples: list[MetricSample] | None = None) -> TestClient:
        async def read() -> list[MetricSample]:
            return samples if samples is not None else []

        app = create_app(
            settings=Settings(),
            assertion_store=_FakeStore(),  # type: ignore[arg-type]
            principal_resolver=_site_a_reader,
            metric_reader=read if samples is not None else None,
        )
        return TestClient(app)

    def test_without_a_reader_it_returns_503(self) -> None:
        """配線されていなければ 503（空を返さない）。"""
        response = self._client().get("/metrics")

        assert response.status_code == 503
        assert "empty exposition" in response.json()["detail"]

    def test_samples_are_exposed_in_prometheus_format(self) -> None:
        """系列が exposition format で出る（HELP / TYPE 付き）。"""
        sample = MetricSample(
            name=metric("probe_auroc"),
            value=0.93,
            at=datetime(2026, 10, 4, 9, tzinfo=UTC),
            labels={"probe": "Running"},
        )

        response = self._client([sample]).get("/metrics")

        assert response.status_code == 200
        body = response.text
        assert "# HELP sbp_grounding_probe_auroc_ratio" in body
        assert "# TYPE sbp_grounding_probe_auroc_ratio gauge" in body
        assert 'sbp_grounding_probe_auroc_ratio{probe="Running"} 0.93' in body

    def test_the_media_type_is_the_prometheus_text_format(self) -> None:
        """媒体型が Prometheus の text format（取り込み側に推測させない）。"""
        response = self._client([]).get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "version=0.0.4" in response.headers["content-type"]

    def test_an_empty_reader_is_a_successful_empty_scrape(self) -> None:
        """配線済みで系列 0 件なら 200（「まだ指標が無い」は正常）。

        503 と区別できることが要点である——未配線は 503、配線済みで空は 200。
        """
        response = self._client([]).get("/metrics")

        assert response.status_code == 200
        assert response.text.strip() == ""
