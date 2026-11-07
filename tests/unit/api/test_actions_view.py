"""アクション契約の要約と判断の状態の資源（M8-22-2、ADR-0021）。

重点:

1. **契約が唯一の正** — 実際の ``actions/*.yaml`` から要約し、UI に写しを持たせない。
2. **承認の要否は条件として返す** — ``medium`` 以上は常に必須、``low`` は契約の条件。
3. **失敗した判断は自動で再試行されない** — 状態 ``failed`` と理由がそのまま出る。
4. **配線が無ければエラー**（空を返さない。所見 32）。要求者がなければ拒否。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.testclient import TestClient

from sbp.actions import ActionRegistry
from sbp.api import Permission, Principal, create_app
from sbp.api.actions_view import summarize
from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.modality import Modality
from sbp.knowledge.decisions import DecisionKind, DecisionStatus

REPO_ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 11, 8, 9, tzinfo=UTC)


def _registry() -> ActionRegistry:
    return ActionRegistry.from_directory(REPO_ROOT / "actions")


def _contracts() -> list[Any]:
    registry = _registry()
    return [registry.get(action_type) for action_type in registry.types()]


class _Store:
    def query(self, **_kwargs: Any) -> list[Any]:
        return []


def _principal(_request: Request) -> Principal:
    return Principal(
        subject="https://sbp.example/id/person/op1",
        permissions=(
            Permission(
                concept_types=frozenset({str(vocab.ent.Running)}),
                modalities=frozenset({Modality.INFERRED}),
                sites=frozenset({"siteA"}),
            ),
        ),
    )


def _client(**readers: Any) -> TestClient:
    return TestClient(
        create_app(
            settings=Settings(),
            assertion_store=_Store(),  # type: ignore[arg-type]
            principal_resolver=_principal,
            **readers,
        )
    )


def _no_decisions(**_kwargs: Any) -> list[DecisionStatus]:
    return []


def _status(*, state: str = "applied", error: str | None = None) -> DecisionStatus:
    return DecisionStatus(
        decision_id="d-1",
        kind=DecisionKind.ACTION,
        subject="https://sbp.example/site/siteA/asset/P-101",
        decided_by="https://sbp.example/person/op1",
        received_at=T0,
        applied_at=T0 + timedelta(seconds=1) if state == "applied" else None,
        failed_at=T0 + timedelta(seconds=1) if state == "failed" else None,
        error=error,
        artifacts={"expectation_id": "e-1"} if state == "applied" else {},
        attempts=1,
    )


class TestSummarize:
    def test_every_registered_contract_is_summarised_in_type_order(self) -> None:
        contracts = _contracts()
        summaries = summarize(contracts)
        assert [item.action_type for item in summaries] == sorted(c.action_type for c in contracts)

    def test_a_medium_or_higher_risk_always_needs_approval(self) -> None:
        for contract, item in zip(
            sorted(_contracts(), key=lambda c: c.action_type), summarize(_contracts()), strict=True
        ):
            if contract.risk_class.requires_approval:
                assert item.approval == "always", contract.action_type

    def test_the_compensation_is_shown_but_there_is_no_way_to_run_it(self) -> None:
        for item in summarize(_contracts()):
            assert item.compensation
            assert item.compensation != item.action_type

    def test_preconditions_and_effects_are_human_readable(self) -> None:
        for item in summarize(_contracts()):
            assert any(item.preconditions.values()), item.action_type
            assert any(item.effects.values()), item.action_type


class TestStatusState:
    def test_a_failed_decision_is_failed_and_keeps_its_reason(self) -> None:
        status = _status(state="failed", error="ActionError: preconditions failed")
        assert status.state == "failed"
        assert status.error == "ActionError: preconditions failed"

    def test_pending_applied_and_failed_are_distinct(self) -> None:
        assert _status(state="applied").state == "applied"
        assert _status(state="pending").state == "pending"
        assert _status(state="failed").state == "failed"


class TestGraphQL:
    def test_action_types_are_served(self) -> None:
        client = _client(action_reader=_contracts)
        data = client.post(
            "/graphql",
            json={
                "query": """{ actionTypes { actionType riskClass approval approvalRole
                  preconditions { face descriptions }
                  effects { face effects { description within } }
                  compensation rateLimit { window max } } }"""
            },
        ).json()
        assert "errors" not in data, data
        rows = data["data"]["actionTypes"]
        assert rows
        assert all(row["compensation"] for row in rows)

    def test_decisions_come_back_with_state_and_reason(self) -> None:
        failed = _status(state="failed", error="ActionError: rate limit")
        seen: dict[str, Any] = {}

        def reader(**kwargs: Any) -> list[DecisionStatus]:
            seen.update(kwargs)
            return [failed, _status(state="applied")]

        client = _client(decision_reader=reader)
        data = client.post(
            "/graphql",
            json={
                "query": """{ decisions(subject: "https://sbp.example/site/siteA/asset/P-101",
                    kinds: ["action"]) { decisionId kind state error artifacts attempts } }"""
            },
        ).json()
        assert "errors" not in data, data
        rows = data["data"]["decisions"]
        assert [row["state"] for row in rows] == ["failed", "applied"]
        assert rows[0]["error"] == "ActionError: rate limit"
        assert json.loads(rows[1]["artifacts"]) == {"expectation_id": "e-1"}
        assert seen["kinds"] == (DecisionKind.ACTION,)

    def test_an_unknown_kind_is_refused(self) -> None:
        client = _client(decision_reader=_no_decisions)
        data = client.post(
            "/graphql", json={"query": '{ decisions(subject: "x", kinds: ["nonsense"]) { state } }'}
        ).json()
        assert "未知の判断の種類" in data["errors"][0]["message"]

    def test_unwired_readers_fail_instead_of_returning_nothing(self) -> None:
        client = _client()
        for query in ("{ actionTypes { actionType } }", '{ decisions(subject: "x") { state } }'):
            errors = client.post("/graphql", json={"query": query}).json()["errors"]
            assert "設定されていない" in errors[0]["message"]

    def test_without_a_principal_both_are_denied(self) -> None:
        app = create_app(
            settings=Settings(),
            assertion_store=_Store(),  # type: ignore[arg-type]
            action_reader=_contracts,
            decision_reader=_no_decisions,
        )
        client = TestClient(app)
        for query in ("{ actionTypes { actionType } }", '{ decisions(subject: "x") { state } }'):
            errors = client.post("/graphql", json={"query": query}).json()["errors"]
            assert "ABAC が設定されていない" in errors[0]["message"]

    def test_the_limit_is_bounded(self) -> None:
        client = _client(decision_reader=_no_decisions)
        errors = client.post(
            "/graphql", json={"query": '{ decisions(subject: "x", limit: 100000) { state } }'}
        ).json()["errors"]
        assert "limit" in errors[0]["message"]
