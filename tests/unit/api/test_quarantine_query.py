"""GraphQL の ``quarantine``（M10-02、ADR-0035）。

重点: **要求者が無ければ拒否**、**自拠点の全概念の許可が無ければ拒否**（生の業務記録を含む）、
**読み出しが無ければエラー（空を返さない）**、未確認だけが既定。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from sbp.api import Permission, Principal, create_app
from sbp.core.config import Settings
from sbp.core.modality import Modality
from sbp.knowledge.quarantine import QuarantineRow

T0 = datetime(2026, 11, 8, 9, tzinfo=UTC)
QUERY = """
query($only: Boolean) {
  quarantine(onlyUnreviewed: $only) {
    eventId topic subject reason payload quarantinedAt reviewedAt resolution
  }
}
"""


class _Store:
    def query(self, **_kwargs: Any) -> list[Any]:
        return []


def _row(*, reviewed: bool = False) -> QuarantineRow:
    return QuarantineRow(
        event_id=uuid4(),
        topic="ingest:asset_register",
        subject="X-1",
        reason="asset_type 'Gizmo' is not a type in the ontology",
        payload={"source": "s", "fields": {"asset_type": "Gizmo"}},
        quarantined_at=T0,
        reviewed_at=T0 if reviewed else None,
        resolution="対応不要" if reviewed else None,
    )


def _reader(rows: list[QuarantineRow]):  # type: ignore[no-untyped-def]
    def read(*, only_unreviewed: bool = True, limit: int = 50) -> list[QuarantineRow]:
        return [r for r in rows if not (only_unreviewed and r.reviewed_at)][:limit]

    return read


def _principal(concepts: frozenset[str], modalities: frozenset[Modality]) -> Principal:
    return Principal(
        subject="https://sbp.example/id/person/op1",
        permissions=(
            Permission(
                concept_types=concepts, modalities=modalities, sites=frozenset({Settings().site_id})
            ),
        ),
    )


def _admin(_request: Request) -> Principal:
    return _principal(frozenset({"*"}), frozenset({Modality.OBSERVED}))


def _one_concept(_request: Request) -> Principal:
    return _principal(
        frozenset({"https://sbp.example/ent#Running"}), frozenset({Modality.OBSERVED})
    )


def _inferred_only(_request: Request) -> Principal:
    return _principal(frozenset({"*"}), frozenset({Modality.INFERRED}))


def _nobody(_request: Request) -> Principal | None:
    return None


def _client(reader: Any, resolver: Any) -> TestClient:
    return TestClient(
        create_app(
            settings=Settings(),
            assertion_store=_Store(),  # type: ignore[arg-type]
            quarantine_reader=reader,
            principal_resolver=resolver,
        )
    )


def _post(client: TestClient, **variables: Any) -> dict[str, Any]:
    return client.post("/graphql", json={"query": QUERY, "variables": variables}).json()


class TestQuarantineQuery:
    def test_rows_are_returned_with_raw_fields_as_json(self) -> None:
        body = _post(_client(_reader([_row()]), _admin))
        assert "errors" not in body, body
        row = body["data"]["quarantine"][0]
        assert row["subject"] == "X-1"
        assert json.loads(row["payload"])["fields"] == {"asset_type": "Gizmo"}
        assert row["reviewedAt"] is None

    def test_only_unreviewed_is_the_default_and_can_be_widened(self) -> None:
        client = _client(_reader([_row(), _row(reviewed=True)]), _admin)
        assert len(_post(client)["data"]["quarantine"]) == 1
        assert len(_post(client, only=False)["data"]["quarantine"]) == 2

    def test_without_a_principal_it_is_refused(self) -> None:
        body = _post(_client(_reader([_row()]), _nobody))
        assert body["errors"] and body["data"] is None

    @pytest.mark.parametrize("resolver", [_one_concept, _inferred_only])
    def test_a_narrow_permission_cannot_read_raw_business_rows(self, resolver: Any) -> None:
        body = _post(_client(_reader([_row()]), resolver))
        assert body["errors"], "全概念の observed が無ければ生の行は見せない"

    def test_an_unwired_reader_is_an_error_not_an_empty_list(self) -> None:
        body = _post(_client(None, _admin))
        assert body["errors"]


class TestReviewCommandAuthorization:
    """確認コマンドは読み出しと同じ許可を要る（書く側にも ABAC）。"""

    def _commands(self, resolver: Any) -> tuple[TestClient, list[str]]:
        seen: list[str] = []
        bound: list[dict[str, Any]] = []
        self.bound = bound

        def handler(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            seen.append(name)
            bound.append(payload)
            return {"accepted": True}

        client = TestClient(
            create_app(
                settings=Settings(),
                assertion_store=_Store(),  # type: ignore[arg-type]
                command_handler=handler,
                principal_resolver=resolver,
            )
        )
        return client, seen

    def test_a_wildcard_reader_may_review(self) -> None:
        client, seen = self._commands(_admin)
        assert client.post("/commands/review_quarantine", json={}).status_code == 200
        assert seen == ["review_quarantine"]
        assert self.bound[0]["reviewed_by"] == "https://sbp.example/id/person/op1", (
            "確認者は要求者から決まる（ADR-0029）"
        )

    @pytest.mark.parametrize("resolver", [_one_concept, _inferred_only])
    def test_a_narrow_principal_cannot_review(self, resolver: Any) -> None:
        client, seen = self._commands(resolver)
        assert client.post("/commands/review_quarantine", json={}).status_code == 403
        assert seen == []


def test_a_blank_or_oversized_resolution_is_refused() -> None:
    from pydantic import ValidationError  # noqa: PLC0415

    from sbp.knowledge.quarantine import QuarantineReviewCommand  # noqa: PLC0415

    base = {"decision_id": "d", "quarantine_id": str(uuid4()), "reviewed_by": "p"}
    for bad in ("   ", "x" * 2001):
        with pytest.raises(ValidationError):
            QuarantineReviewCommand.model_validate({**base, "resolution": bad})
    ok = QuarantineReviewCommand.model_validate({**base, "resolution": "  対応不要  "})
    assert ok.resolution == "対応不要"
