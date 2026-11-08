"""GraphQL の ``scenarios``（M10-04、ADR-0036）。

重点: **要求者が無ければ拒否**、**読み出しが無ければエラー（空を返さない）**、
**``hypothetical`` の許可が無い要求者には見えない**（既定の要求者に見せない）、
許可で絞ってから件数を切る。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from sbp.api import Permission, Principal, create_app
from sbp.api.factory import dev_principal_resolver
from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.modality import Modality
from sbp.knowledge.scenarios import ScenarioRecord

T0 = datetime(2026, 11, 8, 9, tzinfo=UTC)
ENTITY = "https://sbp.example/site/siteA/asset/P-101"
RUNNING = str(vocab.ent.Running)
FAULTED = str(vocab.ent.Faulted)
QUERY = """
query($entity: String, $fluent: String) {
  scenarios(entity: $entity, fluent: $fluent) {
    scenarioId entity fluent origin wmVersion actionType channel magnitude horizon graph note
    points { leadTimeS baseline intervened delta }
  }
}
"""


class _Store:
    def query(self, **_kwargs: Any) -> list[Any]:
        return []


def _record(fluent: str = RUNNING) -> ScenarioRecord:
    sid = uuid4()
    return ScenarioRecord(
        scenario_id=sid,
        entity=ENTITY,
        fluent=fluent,
        wm_version="https://sbp.example/wm/koopman/1.0.0",
        origin=T0,
        action_type="https://sbp.example/ent#StopCmd",
        payload={
            "channel": 0,
            "magnitude": 3.0,
            "horizon": 2,
            "graph": f"urn:sbp:scenario:{sid}",
            "note": "因果の主張としては使わない",
            "lead_times_s": [10.0, 20.0],
            "baseline": [0.5, 0.5],
            "intervened": [0.6, 0.7],
            "delta": [0.1, 0.2],
        },
    )


def _reader(records: list[ScenarioRecord]):  # type: ignore[no-untyped-def]
    def read(
        *, entity: str | None = None, fluent: str | None = None, limit: int = 50
    ) -> list[ScenarioRecord]:
        return [
            r
            for r in records
            if (entity is None or r.entity == entity) and (fluent is None or r.fluent == fluent)
        ][:limit]

    return read


def _principal(modalities: frozenset[Modality], concepts: frozenset[str]) -> Principal:
    return Principal(
        subject="https://sbp.example/id/person/op1",
        permissions=(
            Permission(
                concept_types=concepts, modalities=modalities, sites=frozenset({Settings().site_id})
            ),
        ),
    )


def _hypothetical_all(_request: Request) -> Principal:
    return _principal(frozenset({Modality.HYPOTHETICAL}), frozenset({"*"}))


def _hypothetical_running(_request: Request) -> Principal:
    return _principal(frozenset({Modality.HYPOTHETICAL}), frozenset({RUNNING}))


def _default_viewer(_request: Request) -> Principal:
    modalities = frozenset(
        {Modality.OBSERVED, Modality.INFERRED, Modality.PREDICTED, Modality.PLANNED}
    )
    return _principal(modalities, frozenset({"*"}))


def _nobody(_request: Request) -> Principal | None:
    return None


def _client(reader: Any, resolver: Any) -> TestClient:
    return TestClient(
        create_app(
            settings=Settings(),
            assertion_store=_Store(),  # type: ignore[arg-type]
            scenario_reader=reader,
            principal_resolver=resolver,
        )
    )


def _post(client: TestClient, **variables: Any) -> dict[str, Any]:
    return client.post("/graphql", json={"query": QUERY, "variables": variables}).json()


class TestScenariosQuery:
    def test_the_curves_come_back(self) -> None:
        body = _post(_client(_reader([_record()]), _hypothetical_all))
        assert "errors" not in body, body
        row = body["data"]["scenarios"][0]
        assert row["points"] == [
            {"leadTimeS": 10.0, "baseline": 0.5, "intervened": 0.6, "delta": pytest.approx(0.1)},
            {"leadTimeS": 20.0, "baseline": 0.5, "intervened": 0.7, "delta": pytest.approx(0.2)},
        ]
        assert row["graph"].startswith("urn:sbp:scenario:")
        assert "因果" in row["note"]

    def test_the_default_viewer_cannot_see_hypothetical_scenarios(self) -> None:
        """既定の要求者（hypothetical を含まない）には 1 件も見えない。"""
        body = _post(_client(_reader([_record()]), _default_viewer))
        assert "errors" not in body
        assert body["data"]["scenarios"] == []

    def test_the_dev_resolver_does_not_grant_hypothetical(self) -> None:
        resolver = dev_principal_resolver(Settings())

        class _Req:
            def __init__(self) -> None:
                self.headers = {"X-SBP-Principal": "https://sbp.example/id/person/dev"}

        principal = resolver(_Req())  # type: ignore[arg-type]
        assert principal is not None
        assert not any(Modality.HYPOTHETICAL in p.modalities for p in principal.permissions)

    def test_rows_without_a_concept_permission_are_filtered_before_the_limit(self) -> None:
        records = [_record(FAULTED) for _ in range(5)] + [_record(RUNNING)]
        client = _client(_reader(records), _hypothetical_running)
        rows = client.post("/graphql", json={"query": "{ scenarios(limit: 1) { fluent } }"}).json()[
            "data"
        ]["scenarios"]
        assert [r["fluent"] for r in rows] == [RUNNING]

    def test_without_a_principal_it_is_refused(self) -> None:
        body = _post(_client(_reader([_record()]), _nobody))
        assert body["errors"] and body["data"] is None

    def test_an_unwired_reader_is_an_error_not_an_empty_list(self) -> None:
        body = _post(_client(None, _hypothetical_all))
        assert body["errors"]
