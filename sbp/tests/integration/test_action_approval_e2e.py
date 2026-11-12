"""アクション承認を、コマンドから「人に結果を返す」まで実サービス上で通す（M8-22-4、ADR-0021）。

外界（人の承認・基盤が持っている信念）だけをテストが置き、learning-worker は production の
``build_worker``、API は production の ``build_app``:

    コマンド ``execute_action`` → 判断キュー → learning-worker → ``decisions``（GraphQL）

確かめること:

1. 前提を満たす承認は ``applied`` になり、適用の要約（期待の ID）が返る。
2. 前提未達は ``failed`` になり、**理由がそのまま**返る（人が読める）。自動では再試行されない。
3. 上書き付きの承認は前提未達でも ``applied`` になり、上書きしたことが残る。
4. 同じコマンドの再送は 1 回しか積まれない（二重実行は最悪の失敗）。
5. 契約の要約（``actionTypes``）が API から読める。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from services.learning_worker import build_worker

from sbp.api.commands import build_command_handler
from sbp.api.factory import build_app
from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.ids import site_iri
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.knowledge import AssertionStore, DecisionQueue

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sbp.core.config import Settings
    from sbp.knowledge import HttpGraphStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

OPERATOR = "https://sbp.example/id/person/dev"
INSPECT = str(vocab.ent.CreateEmergencyInspection)


@pytest.fixture
def site(test_graph_suffix: str, assertion_store: AssertionStore) -> str:
    """テストごとの拠点（``assertion_store`` は後片付けのために要求する）。"""
    del assertion_store
    return f"{test_graph_suffix}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
async def connection(
    settings: Settings, site: str, postgres_available: None
) -> AsyncIterator[psycopg.AsyncConnection[tuple[Any, ...]]]:
    del postgres_available
    conn = await psycopg.AsyncConnection.connect(settings.pg_dsn, autocommit=True)
    yield conn
    for table in ("learning_decisions", "expectations"):
        await conn.execute(f"DELETE FROM {table} WHERE site = %s", (site,))  # type: ignore[arg-type]
    await conn.close()


def _belief(entity: str, fluent: str, *, p: float) -> Assertion:
    at = now() - timedelta(minutes=10)
    return Assertion(
        subject=entity,
        predicate=fluent,
        object=True,
        valid_from=at,
        tx_from=at,
        modality=Modality.INFERRED,
        belief=BooleanBelief(p=p),
        provenance="urn:sbp:prov:it-action-approval",
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


def _command(entity: str, *, decision_id: str, override: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "decision_id": decision_id,
        "action_type": INSPECT,
        "target": entity,
        "approved_by": OPERATOR,
        "reason": "異常振動の疑いが強く、緊急点検を起票する",
        "severity": 0.9,
        "correlation_id": str(uuid.uuid4()),
    }
    if override is not None:
        payload["override_reason"] = override
    return payload


def _decisions(settings: Settings, entity: str) -> list[dict[str, Any]]:
    with TestClient(build_app(settings)) as client:
        response = client.post(
            "/graphql",
            json={
                "query": f"""{{ decisions(subject: "{entity}", kinds: ["action"]) {{
                    decisionId kind state error artifacts attempts }} }}"""
            },
            headers={"X-SBP-Principal": OPERATOR},
        )
    payload = response.json()
    assert not payload.get("errors"), payload.get("errors")
    return payload["data"]["decisions"]


class TestApprovalToOutcome:
    async def test_a_satisfied_approval_is_applied_and_reports_its_expectation(
        self,
        settings: Settings,
        site: str,
        graph_store: HttpGraphStore,
        connection: psycopg.AsyncConnection[tuple[Any, ...]],
    ) -> None:
        run = settings.model_copy(update={"site_id": site, "env": "dev"})
        entity = site_iri(site, "asset", "P-101")
        graph = AssertionStore(graph_store, site_id=site)
        graph.assert_(_belief(entity, str(vocab.ent.Accessible), p=0.95))
        graph.assert_(_belief(entity, str(vocab.ent.UnderMaintenance), p=0.05))

        queue = DecisionQueue(connection, site=site)
        handler = build_command_handler(queue.enqueue)
        handler("execute_action", _command(entity, decision_id="ok-1"))
        handler("execute_action", _command(entity, decision_id="ok-1"))  # 再送
        await asyncio.sleep(0.2)
        assert len(await queue.history(subject=entity)) == 1, "再送は 1 回しか積まれない"

        worker, _queue = await build_worker(run, site_id=site)
        try:
            assert await worker.step() == 1
        finally:
            await worker.aclose()

        rows = await asyncio.to_thread(_decisions, run, entity)
        assert [row["state"] for row in rows] == ["applied"]
        artifacts = json.loads(rows[0]["artifacts"])
        assert artifacts["expectation_id"]
        assert artifacts["action_type"] == INSPECT
        assert artifacts["overridden"] is False

    async def test_a_failed_precondition_returns_its_reason_and_is_not_retried(
        self,
        settings: Settings,
        site: str,
        connection: psycopg.AsyncConnection[tuple[Any, ...]],
    ) -> None:
        run = settings.model_copy(update={"site_id": site, "env": "dev"})
        entity = site_iri(
            site, "asset", "P-102"
        )  # 信念が無い（知らないことを満たしたとみなさない）

        queue = DecisionQueue(connection, site=site)
        build_command_handler(queue.enqueue)("execute_action", _command(entity, decision_id="no-1"))
        await asyncio.sleep(0.2)

        worker, _queue = await build_worker(run, site_id=site)
        try:
            assert await worker.step() == 0
            assert await worker.step() == 0, "失敗した判断は再試行されない"
        finally:
            await worker.aclose()

        rows = await asyncio.to_thread(_decisions, run, entity)
        assert [row["state"] for row in rows] == ["failed"]
        assert "preconditions" in rows[0]["error"]
        assert rows[0]["attempts"] == 1

    async def test_an_override_runs_despite_the_failed_precondition_and_is_recorded(
        self,
        settings: Settings,
        site: str,
        connection: psycopg.AsyncConnection[tuple[Any, ...]],
    ) -> None:
        run = settings.model_copy(update={"site_id": site, "env": "dev"})
        entity = site_iri(site, "asset", "P-103")

        queue = DecisionQueue(connection, site=site)
        build_command_handler(queue.enqueue)(
            "execute_action",
            _command(entity, decision_id="ov-1", override="現場で近づけることを確認した"),
        )
        await asyncio.sleep(0.2)

        worker, _queue = await build_worker(run, site_id=site)
        try:
            assert await worker.step() == 1
        finally:
            await worker.aclose()

        rows = await asyncio.to_thread(_decisions, run, entity)
        assert [row["state"] for row in rows] == ["applied"]
        assert json.loads(rows[0]["artifacts"])["overridden"] is True


class TestContractsAreReadable:
    def test_action_types_come_from_the_contracts(self, settings: Settings) -> None:
        dev = settings.model_copy(update={"env": "dev"})
        with TestClient(build_app(dev)) as client:
            response = client.post(
                "/graphql",
                json={
                    "query": """{ actionTypes { actionType riskClass approval compensation
                      preconditions { face descriptions } } }"""
                },
                headers={"X-SBP-Principal": OPERATOR},
            )
        payload = response.json()
        assert not payload.get("errors"), payload.get("errors")
        found = {row["actionType"]: row for row in payload["data"]["actionTypes"]}
        assert INSPECT in found
        assert found[INSPECT]["approval"] == "always", "medium は深刻度に依らず承認が必須"
        assert found[INSPECT]["compensation"]
        faces = {face["face"] for face in found[INSPECT]["preconditions"]}
        assert faces == {"symbolic", "physical", "normative"}
