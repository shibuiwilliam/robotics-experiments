"""GraphQL の ``forecasts``（M9-03、ADR-0032）。

重点: **要求者が無ければ拒否**、**読み出しが無ければエラー（空を返さない）**、
**概念型×様相×拠点の許可が無い行は返さない**、**標本そのものは返さない**。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from sbp.api import Permission, Principal, create_app
from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.modality import Modality
from sbp.knowledge.forecasts import ForecastRecord

T0 = datetime(2026, 11, 8, 9, tzinfo=UTC)
ENTITY = "https://sbp.example/site/siteA/asset/P-101"
RUNNING = str(vocab.ent.Running)
FAULTED = str(vocab.ent.Faulted)
QUERY = """
query($entity: String, $fluent: String) {
  forecasts(entity: $entity, fluent: $fluent) {
    forecastId entity fluent horizonOrigin wmVersion intervalLevel scored
    points { leadTimeS mean lower upper sampleCount }
  }
}
"""


class _Store:
    def query(self, **_kwargs: Any) -> list[Any]:
        return []


def _record(fluent: str = RUNNING, *, scored: bool = False) -> ForecastRecord:
    return ForecastRecord(
        forecast_id=uuid4(),
        entity=ENTITY,
        fluent=fluent,
        horizon_origin=T0,
        horizon_end=T0 + timedelta(seconds=100),
        wm_version="https://sbp.example/wm/koopman/1.0.0",
        interval_level=0.9,
        payload={
            "points": [
                {
                    "lead_time_s": 50.0,
                    "mean": 0.7,
                    "lower": 0.6,
                    "upper": 0.8,
                    "samples": [0.6, 0.7, 0.8],
                }
            ]
        },
        scored_at=T0 if scored else None,
    )


def _reader(records: list[ForecastRecord]):  # type: ignore[no-untyped-def]
    def read(
        *, entity: str | None = None, fluent: str | None = None, limit: int = 50
    ) -> list[ForecastRecord]:
        del limit
        return [
            r
            for r in records
            if (entity is None or r.entity == entity) and (fluent is None or r.fluent == fluent)
        ]

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


def _client(reader: Any, resolver: Any) -> TestClient:
    return TestClient(
        create_app(
            settings=Settings(),
            assertion_store=_Store(),  # type: ignore[arg-type]
            forecast_reader=reader,
            principal_resolver=resolver,
        )
    )


def _post(client: TestClient, **variables: Any) -> dict[str, Any]:
    return client.post("/graphql", json={"query": QUERY, "variables": variables}).json()


def _allow_all(_request: Request) -> Principal:
    return _principal(frozenset({Modality.PREDICTED}), frozenset({"*"}))


def _running_only(_request: Request) -> Principal:
    return _principal(frozenset({Modality.PREDICTED}), frozenset({RUNNING}))


def _inferred_only(_request: Request) -> Principal:
    return _principal(frozenset({Modality.INFERRED}), frozenset({"*"}))


def _nobody(_request: Request) -> Principal | None:
    return None


class TestForecastsQuery:
    def test_forecasts_are_returned_without_samples(self) -> None:
        body = _post(_client(_reader([_record(scored=True)]), _allow_all))
        assert "errors" not in body, body
        row = body["data"]["forecasts"][0]
        assert row["entity"] == ENTITY
        assert row["scored"] is True
        assert row["points"] == [
            {"leadTimeS": 50.0, "mean": 0.7, "lower": 0.6, "upper": 0.8, "sampleCount": 3}
        ]

    def test_filters_reach_the_reader(self) -> None:
        reader = _reader([_record(RUNNING), _record(FAULTED)])
        body = _post(_client(reader, _allow_all), fluent=FAULTED)
        assert [r["fluent"] for r in body["data"]["forecasts"]] == [FAULTED]

    def test_rows_without_a_permission_are_not_returned(self) -> None:
        reader = _reader([_record(RUNNING), _record(FAULTED)])
        body = _post(_client(reader, _running_only))
        assert [r["fluent"] for r in body["data"]["forecasts"]] == [RUNNING]

    def test_a_principal_without_the_predicted_modality_sees_nothing(self) -> None:
        body = _post(_client(_reader([_record()]), _inferred_only))
        assert body["data"]["forecasts"] == []

    def test_no_principal_is_refused(self) -> None:
        body = _post(_client(_reader([_record()]), _nobody))
        assert body["errors"], "既定は拒否"

    def test_an_unwired_reader_is_an_error_not_an_empty_list(self) -> None:
        body = _post(_client(None, _allow_all))
        assert body["errors"]
        assert "forecast_reader" in body["errors"][0]["message"]

    def test_the_limit_is_bounded(self) -> None:
        client = _client(_reader([]), _allow_all)
        response = client.post(
            "/graphql", json={"query": "{ forecasts(limit: 0) { forecastId } }"}
        ).json()
        assert response["errors"]


@pytest.mark.parametrize("fluent", [RUNNING])
def test_the_reader_is_passed_keyword_arguments_only(fluent: str) -> None:
    calls: list[dict[str, Any]] = []

    def read(**kwargs: Any) -> list[ForecastRecord]:
        calls.append(kwargs)
        return []

    _post(_client(read, _allow_all), fluent=fluent)
    assert calls == [{"entity": None, "fluent": fluent, "limit": 50}]
