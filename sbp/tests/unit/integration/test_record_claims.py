"""記録の主張と主張イベントの単体テスト（M8-25-3、ADR-0016 の問い 2、所見 115）。

記録には「存在する」と「何かを主張する」の 2 つの事実がある。前者は確実（``p = 1.0``）、後者は
情報源の信頼性で割り引かれる。照合ワーカーと信念ワーカーが読むのは後者で、フルーエント型
（``…/ent:Running``）と、信頼性モデルを通した確率を持つイベントである。

重点:

1. **主張は写像で宣言する** — どの記録型のどの列がどのフルーエントの主張かを 1 か所に置く。
   宣言のフルーエントはオントロジーに実在し、列は ``fields`` に存在しなければ読み込みで落ちる。
2. **確率は信頼性モデルから作る** — MES が「稼働」と言っても、テンプレ入力があるので 0.79 の主張に
   すぎない（混同行列 TP 0.95 / FP 0.25、一様事前）。
3. **読み手と契約が合う** — 照合・信念ワーカーの ``fluent_of_event`` が主張イベントから
   フルーエントを取り出せる（段と段の間の契約。所見 115）。
4. **読み直しても同じイベントになる** — ``event_id`` は内容から導く（決定的な UUIDv7）。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from sbp.core import vocab
from sbp.core.errors import IntegrationError, MappingError
from sbp.core.modality import Modality
from sbp.integration import (
    IngestionPipeline,
    MappingRegistry,
    MasterDataResolver,
    ReliabilityRegistry,
)
from sbp.integration.connectors import RawRecord
from sbp.integration.mappings import Mapping
from sbp.reasoning.ingest import fluent_of_event

REPO_ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
VERSION = vocab.ONTOLOGY_VERSION
PUMP = "https://sbp.example/site/siteA/asset/P-101"


def _operation_log_row(*, running: object, updated_at: str = "2026-09-12T09:05:00Z") -> RawRecord:
    return RawRecord(
        source=str(vocab.ent.MesSource),
        table="operation_log",
        external_id="OL-0912-A",
        fields={
            "log_id": "OL-0912-A",
            "asset_tag": "P-101",
            "running": running,
            "shift": "shift1",
            "logged_at": "2026-09-12T09:00:00Z",
            "recorded_at": "2026-09-12T09:05:00Z",
            "updated_at": updated_at,
        },
        fetched_at=T0,
    )


def _mapping(**overrides: Any) -> dict[str, Any]:
    """``operation_log`` の写像宣言（主張の宣言を差し替えて検査する）。"""
    declaration: dict[str, Any] = {
        "mapping": "operation_log",
        "version": "1.0.0",
        "source": "ent:MesSource",
        "table": "operation_log",
        "record_type": "ent:OperationLog",
        "key": {"columns": ["log_id"], "external_id": "{log_id}"},
        "subject_ref": {"column": "asset_tag", "kind": "asset"},
        "required": ["log_id", "asset_tag", "running", "logged_at", "recorded_at"],
        "time": {"recorded_at": "recorded_at", "occurred_at": "logged_at"},
        "fields": {"running": "running", "shift": "shift"},
        "claims": [{"fluent": "ent:Running", "field": "running"}],
    }
    declaration.update(overrides)
    return declaration


@pytest.fixture(scope="module")
def pipeline() -> IngestionPipeline:
    resolver = MasterDataResolver(site_id="siteA")
    resolver.register("asset", "P-101")
    return IngestionPipeline(
        site_id="siteA",
        mappings=MappingRegistry.from_directory(REPO_ROOT / "mappings"),
        resolver=resolver,
        reliability=ReliabilityRegistry.from_directory(REPO_ROOT / "reliability"),
        ontology_version=VERSION,
    )


class TestClaimDeclaration:
    def test_the_fluent_is_expanded_to_an_iri(self) -> None:
        mapping = Mapping.model_validate(_mapping())
        assert mapping.claims[0].fluent == str(vocab.ent.Running)

    def test_an_unknown_fluent_is_refused(self) -> None:
        """綴り違いを通すと、主張が永久にどのフルーエントにも当たらない。"""
        with pytest.raises(ValueError, match="not a FluentType"):
            Mapping.model_validate(_mapping(claims=[{"fluent": "ent:Runing", "field": "running"}]))

    def test_a_claim_on_an_unmapped_field_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not in `fields`"):
            Mapping.model_validate(_mapping(claims=[{"fluent": "ent:Running", "field": "speed"}]))

    def test_the_repository_declares_the_mes_running_claim(self) -> None:
        mapping = MappingRegistry.from_directory(REPO_ROOT / "mappings").get("operation_log")
        assert [claim.fluent for claim in mapping.claims] == [str(vocab.ent.Running)]


class TestClaimValues:
    @pytest.mark.parametrize(
        ("value", "says"), [(1, True), (0, False), (True, True), ("true", True), ("0", False)]
    )
    def test_boolean_values_are_read(self, value: object, says: bool) -> None:
        mapping = Mapping.model_validate(_mapping())
        record = mapping.apply(_operation_log_row(running=value), ontology_version=VERSION).record
        assert [claim.says for claim in mapping.claims_of(record)] == [says]

    def test_an_unreadable_value_is_an_error(self) -> None:
        """推測で真偽に寄せない（``"maybe"`` を偽と読むと、稼働の主張が停止の主張に化ける）。"""
        mapping = Mapping.model_validate(_mapping())
        record = mapping.apply(_operation_log_row(running="maybe"), ontology_version=VERSION).record
        with pytest.raises(MappingError, match="cannot read"):
            mapping.claims_of(record)


class TestClaimEvents:
    def test_a_claim_event_accompanies_the_existence_event(
        self, pipeline: IngestionPipeline
    ) -> None:
        result = pipeline.ingest_rows([_operation_log_row(running=1)])
        types = [event.type for _topic, event in result.events]
        assert types[0] == str(vocab.ent.OperationLog), "記録が存在するイベントは残す"
        assert len(types) == 2

    def test_the_claim_event_is_readable_by_the_reasoning_workers(
        self, pipeline: IngestionPipeline
    ) -> None:
        """照合・信念ワーカーの読み方と契約が合う（所見 115）。"""
        _topic, claim = pipeline.ingest_rows([_operation_log_row(running=1)]).events[1]
        assert fluent_of_event(str(claim.type)) == "Running"
        assert claim.modality is Modality.OBSERVED, "照合ワーカーは observed を記録列に入れる"
        assert claim.subject == PUMP

    def test_a_running_claim_is_discounted_by_the_reliability_model(
        self, pipeline: IngestionPipeline
    ) -> None:
        """MES の「稼働」は TP 0.95 / FP 0.25 なので 0.95 / (0.95 + 0.25)。"""
        _topic, claim = pipeline.ingest_rows([_operation_log_row(running=1)]).events[1]
        assert claim.belief.p == pytest.approx(0.95 / 1.20)  # type: ignore[union-attr]

    def test_a_stopped_claim_is_discounted_too(self, pipeline: IngestionPipeline) -> None:
        """「停止」は (1 - 0.95) / ((1 - 0.95) + (1 - 0.25))。"""
        _topic, claim = pipeline.ingest_rows([_operation_log_row(running=0)]).events[1]
        assert claim.belief.p == pytest.approx(0.05 / 0.80)  # type: ignore[union-attr]

    def test_reading_the_same_row_again_gives_the_same_events(
        self, pipeline: IngestionPipeline
    ) -> None:
        """取込は at-least-once。読み直しが消費側の冪等記録で弾かれる。"""
        first = [
            e.event_id for _t, e in pipeline.ingest_rows([_operation_log_row(running=1)]).events
        ]
        again = [
            e.event_id for _t, e in pipeline.ingest_rows([_operation_log_row(running=1)]).events
        ]
        assert first == again
        assert len(set(first)) == 2, "存在と主張は別のイベント"

    def test_a_business_side_correction_is_a_new_event(self, pipeline: IngestionPipeline) -> None:
        """業務側の訂正（更新時刻が変わる）は新しい主張として扱う。"""
        before = pipeline.ingest_rows([_operation_log_row(running=1)]).events[1][1]
        after = pipeline.ingest_rows(
            [_operation_log_row(running=0, updated_at="2026-09-13T10:00:00Z")]
        ).events[1][1]
        assert before.event_id != after.event_id

    def test_records_without_claims_emit_only_the_existence_event(
        self, pipeline: IngestionPipeline
    ) -> None:
        row = RawRecord(
            source=str(vocab.ent.CmmsSource),
            table="work_order",
            external_id="WO-123",
            fields={
                "wo_number": "WO-123",
                "asset_tag": "P-101",
                "wo_type": "BearingReplacement",
                "status": "Completed",
                "recorded_at": "2026-09-13T11:00:00Z",
            },
            fetched_at=T0,
        )
        assert len(pipeline.ingest_rows([row]).events) == 1

    def test_a_claim_needs_a_confusion_matrix(self) -> None:
        """真偽を主張しない記録型（混同行列なし）に主張を宣言するのは設定の誤り。"""
        mapping = Mapping.model_validate(
            _mapping(
                source="ent:CmmsSource",
                record_type="ent:MaintenancePlan",
            )
        )
        pipeline = IngestionPipeline(
            site_id="siteA",
            mappings=MappingRegistry({"operation_log": mapping}),
            resolver=MasterDataResolver(site_id="siteA"),
            reliability=ReliabilityRegistry.from_directory(REPO_ROOT / "reliability"),
            ontology_version=VERSION,
        )
        row = _operation_log_row(running=1)
        with pytest.raises(IntegrationError, match="no confusion matrix"):
            pipeline.ingest_rows([replace(row, source=str(vocab.ent.CmmsSource))])


def test_the_same_record_at_another_site_is_another_event() -> None:
    """冪等記録の鍵は ``(consumer_group, event_id)``。拠点が違えば別のイベントになる。"""
    events = []
    for site in ("siteA", "siteB"):
        resolver = MasterDataResolver(site_id=site)
        resolver.register("asset", "P-101")
        pipeline = IngestionPipeline(
            site_id=site,
            mappings=MappingRegistry.from_directory(REPO_ROOT / "mappings"),
            resolver=resolver,
            reliability=ReliabilityRegistry.from_directory(REPO_ROOT / "reliability"),
            ontology_version=VERSION,
        )
        events.append(pipeline.ingest_rows([_operation_log_row(running=1)]).events[1][1])
    assert events[0].event_id != events[1].event_id
