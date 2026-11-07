"""証拠の三源並置（M8-17-3、ADR-0020）。

重点:

1. **寄与が無い点を寄与 0 と区別する** — ``contribution`` が ``None`` は「記録されていない」。
2. **結合が書いた信念は出どころが ``coupling``** — 寄与を持たない。
3. **区間は右開** — 始端を含み、終端を含まない。
4. **ABAC** — 見えない文の寄与は出さない。寄与の行だけ読めても、文が見えなければ出さない。
5. **配線が無ければエラー**（空を返さない。所見 32）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from sbp.api import Permission, Principal, create_app
from sbp.api.evidence import build_evidence
from sbp.api.schema import DiscrepancyType
from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.modality import Modality
from sbp.knowledge.assertions import AssertionRow
from sbp.knowledge.contributions import ContributionRecord
from sbp.knowledge.discrepancies import DiscrepancyRecord

T0 = datetime(2026, 11, 7, 9, tzinfo=UTC)
END = T0 + timedelta(hours=1)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
RUNNING = str(vocab.ent.Running)


def _row(
    *,
    at: datetime = T0,
    confidence: float = 0.8,
    modality: Modality = Modality.INFERRED,
    provenance: str = "urn:sbp:prov:belief-worker/evt-1",
    valid_to: datetime | None = None,
    graph: str = "urn:sbp:g:inferred:siteA:0.6.0",
) -> AssertionRow:
    return AssertionRow(
        assertion_id=uuid4(),
        subject=PUMP,
        predicate=RUNNING,
        object_="true",
        valid_from=at,
        valid_to=valid_to,
        tx_from=at,
        tx_to=None,
        modality=modality,
        confidence=confidence,
        provenance=provenance,
        ontology_version=vocab.ONTOLOGY_VERSION,
        graph=graph,
    )


def _contribution(
    row: AssertionRow, *, observed: float = 0.0, record: float = 0.0, plan: float = 0.0
) -> ContributionRecord:
    return ContributionRecord(
        assertion_id=row.assertion_id,
        entity=PUMP,
        fluent=RUNNING,
        at=row.valid_from,
        prior=0.5,
        posterior=row.confidence,
        observed=observed,
        record=record,
        plan=plan,
        source_event_id="evt-1",
    )


class TestBuildEvidence:
    def test_a_point_without_a_contribution_is_not_a_zero_contribution(self) -> None:
        row = _row()
        points, _ = build_evidence([row], [], [], start=T0, end=END)
        assert points[0].contribution is None, "「記録されていない」は 0 ではない"

    def test_a_recorded_contribution_is_attached_by_assertion_id(self) -> None:
        row = _row()
        contribution = _contribution(row, record=2.0)
        points, _ = build_evidence([row], [], [contribution], start=T0, end=END)
        assert points[0].contribution == contribution
        assert points[0].origin == "update"

    def test_a_belief_written_by_coupling_is_marked_as_such(self) -> None:
        row = _row(provenance="urn:sbp:prov:belief-worker/coupling/2026-11-07T09:00:00+00:00")
        points, _ = build_evidence([row], [], [], start=T0, end=END)
        assert points[0].origin == "coupling"

    def test_an_orphan_contribution_is_not_a_point(self) -> None:
        orphan = _contribution(_row(), observed=1.0)
        points, _ = build_evidence([], [], [orphan], start=T0, end=END)
        assert points == ()

    def test_the_range_is_right_open_and_points_are_ordered(self) -> None:
        inside = [_row(at=T0 + timedelta(minutes=m)) for m in (30, 0, 10)]
        outside = _row(at=END)
        points, _ = build_evidence([*inside, outside], [], [], start=T0, end=END)
        assert [p.at for p in points] == [
            T0,
            T0 + timedelta(minutes=10),
            T0 + timedelta(minutes=30),
        ]

    def test_planned_spans_overlapping_the_range_are_kept(self) -> None:
        before = _row(
            at=T0 - timedelta(hours=3), valid_to=T0 - timedelta(hours=1), modality=Modality.PLANNED
        )
        spanning = _row(
            at=T0 - timedelta(hours=1),
            valid_to=T0 + timedelta(minutes=5),
            modality=Modality.PLANNED,
        )
        open_ended = _row(at=T0 + timedelta(minutes=30), modality=Modality.PLANNED)
        after = _row(at=END, modality=Modality.PLANNED)
        _, spans = build_evidence([], [before, spanning, open_ended, after], [], start=T0, end=END)
        assert [(s.start, s.end) for s in spans] == [
            (spanning.valid_from, spanning.valid_to),
            (open_ended.valid_from, None),
        ]


class _Store:
    """modality で絞る代役。"""

    def __init__(self, rows: list[AssertionRow]) -> None:
        self.rows = rows

    def query(self, **kwargs: Any) -> list[AssertionRow]:
        return [row for row in self.rows if row.modality is kwargs.get("modality")]


def _principal(*modalities: Modality) -> Principal:
    return Principal(
        subject="https://sbp.example/id/person/op1",
        permissions=(
            Permission(
                concept_types=frozenset({RUNNING}),
                modalities=frozenset(modalities),
                sites=frozenset({"siteA"}),
            ),
        ),
    )


def _client(
    rows: list[AssertionRow],
    contributions: list[ContributionRecord],
    *,
    principal: Principal | None,
    wired: bool = True,
) -> TestClient:
    def resolver(_request: Request) -> Principal | None:
        return principal

    def reader(
        entity: str, fluent: str, start: datetime, end: datetime
    ) -> list[ContributionRecord]:
        del entity, fluent, start, end
        return contributions

    app = create_app(
        settings=Settings(),
        assertion_store=_Store(rows),  # type: ignore[arg-type]
        principal_resolver=resolver,
        evidence_reader=reader if wired else None,
    )
    return TestClient(app)


_QUERY = (
    f'{{ beliefEvidence(entity: "{PUMP}", fluent: "{RUNNING}", '
    f'start: "{T0.isoformat()}", end: "{END.isoformat()}") '
    "{ points { assertionId probability origin contribution "
    "{ observed record plan dominant sourceEventId excludedRecords } } "
    "planned { adherence } } }"
)


def _ask(client: TestClient) -> dict[str, Any]:
    return client.post("/graphql", json={"query": _QUERY}).json()


class TestGraphQL:
    def test_three_sources_come_back_side_by_side(self) -> None:
        probe = _row(at=T0)
        claim = _row(at=T0 + timedelta(minutes=5), confidence=0.9)
        plan = _row(
            at=T0 - timedelta(hours=1), valid_to=END, modality=Modality.PLANNED, confidence=0.9
        )
        contributions = [
            _contribution(probe, observed=2.3),
            _contribution(claim, record=1.3, plan=0.4),
        ]
        client = _client(
            [probe, claim, plan],
            contributions,
            principal=_principal(Modality.INFERRED, Modality.PLANNED),
        )
        data = _ask(client)["data"]["beliefEvidence"]
        assert [p["contribution"]["dominant"] for p in data["points"]] == ["observed", "record"]
        assert data["points"][1]["contribution"]["plan"] == pytest.approx(0.4)
        assert data["planned"] == [{"adherence": pytest.approx(0.9)}]

    def test_an_unrecorded_point_returns_null_not_zero(self) -> None:
        client = _client([_row()], [], principal=_principal(Modality.INFERRED))
        data = _ask(client)["data"]["beliefEvidence"]
        assert data["points"][0]["contribution"] is None

    def test_planned_beliefs_are_hidden_without_the_permission(self) -> None:
        plan = _row(at=T0 - timedelta(hours=1), valid_to=END, modality=Modality.PLANNED)
        client = _client([_row(), plan], [], principal=_principal(Modality.INFERRED))
        data = _ask(client)["data"]["beliefEvidence"]
        assert data["planned"] == []
        assert len(data["points"]) == 1

    def test_a_belief_the_principal_cannot_see_does_not_leak_its_contribution(self) -> None:
        other_site = _row(graph="urn:sbp:g:inferred:siteB:0.6.0")
        client = _client(
            [other_site],
            [_contribution(other_site, observed=3.0)],
            principal=_principal(Modality.INFERRED),
        )
        assert _ask(client)["data"]["beliefEvidence"]["points"] == []

    def test_without_a_principal_it_is_denied(self) -> None:
        client = _client([_row()], [], principal=None)
        errors = _ask(client)["errors"]
        assert "ABAC が設定されていない" in errors[0]["message"]

    def test_without_the_reader_it_fails_rather_than_returning_nothing(self) -> None:
        client = _client([_row()], [], principal=_principal(Modality.INFERRED), wired=False)
        assert "evidence_reader" in _ask(client)["errors"][0]["message"]

    def test_an_empty_range_is_refused(self) -> None:
        client = _client([], [], principal=_principal(Modality.INFERRED))
        query = _QUERY.replace(END.isoformat(), T0.isoformat())
        errors = client.post("/graphql", json={"query": query}).json()["errors"]
        assert "start < end" in errors[0]["message"]


def test_uuid_ids_round_trip() -> None:
    row = _row()
    client = _client([row], [], principal=_principal(Modality.INFERRED))
    point = _ask(client)["data"]["beliefEvidence"]["points"][0]
    assert UUID(point["assertionId"]) == row.assertion_id


class TestDiscrepancyInterval:
    """不一致の区間が API に出る（証拠の三源並置の鍵。M8-17）。"""

    def test_the_interval_comes_from_the_payload(self) -> None:
        record = DiscrepancyRecord(
            discrepancy_id=uuid4(),
            kind=2,
            status="Open",
            status_rank=0,
            subjects=(PUMP,),
            severity_score=0.5,
            detected_at=T0,
            payload={"interval": {"start": T0.isoformat(), "end": END.isoformat()}},
        )
        found = DiscrepancyType.from_record(record)
        assert found.intervalStart == T0
        assert found.intervalEnd == END

    def test_a_missing_interval_is_null_not_an_error(self) -> None:
        record = DiscrepancyRecord(
            discrepancy_id=uuid4(),
            kind=2,
            status="Open",
            status_rank=0,
            subjects=(PUMP,),
            severity_score=0.5,
            detected_at=T0,
            payload={},
        )
        found = DiscrepancyType.from_record(record)
        assert found.intervalStart is None
        assert found.intervalEnd is None
