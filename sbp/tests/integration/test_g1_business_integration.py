"""ゲート G1 の統合テスト（docs/TASK.md M1-09）。

検収内容: モック業務システムの台本 → 写像 → BusinessEvent → 知識グラフに ``planned`` /
記録由来アサーションが載る。

これは M1 の主張「業務記録・計画・規範が信頼性メタ付きの主張として入る」を実機で確かめる。
設計書 §2.5（記録パス）、§7.2〜§7.4 に対応。
"""

from __future__ import annotations

import asyncio
import math
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from tests.integration.conftest import REPO_ROOT

from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.events import EventLayer, topic_name
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.eventlog import (
    ConsumerPolicy,
    InMemoryIdempotencyStore,
    KafkaEventProducer,
    process_once,
)
from sbp.integration import (
    IngestionPipeline,
    MappingRegistry,
    MasterDataResolver,
    ReliabilityRegistry,
    WritebackCommand,
    WritebackExecutor,
    WritebackResult,
    WritebackTarget,
    plan_item_from_record,
    to_planned_assertion,
)
from sbp.integration.mock import MockBusinessSystem
from sbp.integration.mock.business_system import build_default_scenario
from sbp.knowledge import HttpGraphStore, schema_graph

if TYPE_CHECKING:
    from sbp.core.config import Settings
    from sbp.core.events import EventEnvelope
    from sbp.knowledge import AssertionStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

T0 = now().replace(microsecond=0)


@pytest.fixture(scope="module")
def mappings() -> MappingRegistry:
    return MappingRegistry.from_directory(REPO_ROOT / "mappings")


@pytest.fixture(scope="module")
def reliability() -> ReliabilityRegistry:
    return ReliabilityRegistry.from_directory(REPO_ROOT / "reliability")


@pytest.fixture
def pipeline(
    mappings: MappingRegistry, reliability: ReliabilityRegistry, test_graph_suffix: str
) -> IngestionPipeline:
    """P-101 と SKU-77 を知っているマスタを持つ取り込み経路。"""
    resolver = MasterDataResolver(site_id=test_graph_suffix)
    resolver.register("asset", "P-101")
    resolver.register("material", "SKU-77")
    return IngestionPipeline(
        site_id=test_graph_suffix,
        mappings=mappings,
        resolver=resolver,
        reliability=reliability,
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


class TestG1RecordPathReachesTheEventLog:
    """台本 → 写像 → BusinessEvent → イベントログ（実ブローカー）。"""

    async def test_business_event_round_trips_through_the_broker(
        self, settings: Settings, kafka_available: None, pipeline: IngestionPipeline
    ) -> None:
        """業務記録由来のイベントが実ブローカーを往復する。"""
        del kafka_available
        with MockBusinessSystem(table="operation_log") as mock:
            mock.apply(build_default_scenario(T0))
            rows = list(await mock.pull())

        result = pipeline.ingest_rows(rows)
        assert result.failed == []
        # 記録が存在するイベントと Running の主張イベント（ADR-0016）
        assert len(result.events) == 2
        _, event = result.events[0]

        topic = topic_name(settings.site_id, EventLayer.INTEGRATION, "business")
        async with KafkaEventProducer(settings) as producer:
            await producer.send(topic, event)
            await producer.flush()

        # 冪等化を通して処理する（consumer の往復は G0 で検証済みなので、ここでは
        # 業務イベントが処理経路に乗ることを確かめる）
        seen: list[EventEnvelope] = []

        async def handler(e: EventEnvelope) -> None:
            seen.append(e)

        store = InMemoryIdempotencyStore()
        policy = ConsumerPolicy(consumer_group=f"g1-{event.event_id.hex[:8]}")
        assert await process_once(event, topic=topic, handler=handler, store=store, policy=policy)
        assert len(seen) == 1
        assert seen[0].evidence[0].kind == "record"
        assert seen[0].evidence[0].source == vocab.ent.MesSource
        assert seen[0].evidence[0].source_reliability is not None, (
            "信頼性モデルの参照が証拠に入っている（設計書 §7.3）"
        )


class TestG1PlannedAssertionsLandInTheGraph:
    """計画 → planned アサーション → 知識グラフ（実 Oxigraph）。"""

    def test_plan_becomes_a_planned_assertion_in_the_store(
        self, assertion_store: AssertionStore, pipeline: IngestionPipeline
    ) -> None:
        """設計書 §14: 計画が遵守率 0.9 の planned アサーションとして載る。"""

        with MockBusinessSystem(table="maintenance_plan") as mock:
            mock.apply(build_default_scenario(T0))
            rows = list(asyncio.run(mock.pull()))

        result = pipeline.ingest_rows(rows)
        assert len(result.records) == 1
        plan = plan_item_from_record(result.records[0], intended_fluent=vocab.ent.Running)
        assertion = to_planned_assertion(plan, site_id="siteA", tx_from=now())
        assertion_store.assert_(assertion)

        rows_back = assertion_store.query(modality=Modality.PLANNED)
        assert len(rows_back) == 1
        row = rows_back[0]
        assert row.predicate == vocab.ent.Running
        assert row.confidence == pytest.approx(0.9), "遵守率の事前分布がそのまま確信度になる"
        assert row.ontology_version == vocab.ONTOLOGY_VERSION
        assert ":planned:" in row.graph, "planned は様相ごとの名前付きグラフに入る"

    def test_planned_and_record_derived_beliefs_coexist(
        self, assertion_store: AssertionStore, pipeline: IngestionPipeline
    ) -> None:
        """三源のうち「計画」と「記録」が同じ実体について別々の様相で共存する。

        設計書 §14 の「計画: 稼働予定、記録: 稼働中」に対応する。両者が別の様相で共存し、
        観測と突き合わせられる状態になっているのが M1 の到達点である。
        """

        with MockBusinessSystem(table="maintenance_plan") as plan_mock:
            plan_mock.apply(build_default_scenario(T0))
            plan_rows = list(asyncio.run(plan_mock.pull()))
        plan = plan_item_from_record(
            pipeline.ingest_rows(plan_rows).records[0], intended_fluent=vocab.ent.Running
        )
        assertion_store.assert_(to_planned_assertion(plan, site_id="siteA", tx_from=now()))

        # 記録由来の信念（MES が稼働中と言っている → 信頼性モデルの尤度比から確信度を置く）
        reliability = ReliabilityRegistry.from_directory(REPO_ROOT / "reliability")
        model = reliability.get(vocab.ent.MesSource, vocab.ent.OperationLog)
        assert model.confusion is not None
        # 事前 0.5 に記録の尤度比を 1 回足した事後（本格的な統合は M3-02）

        posterior_odds = math.exp(model.confusion.log_likelihood_ratio(record_says=True))
        posterior = posterior_odds / (1.0 + posterior_odds)

        assertion_store.assert_(
            Assertion(
                subject=plan.target,
                predicate=vocab.ent.Running,
                object=True,
                valid_from=T0,
                tx_from=now(),
                modality=Modality.INFERRED,
                belief=BooleanBelief(p=posterior),
                provenance="https://sbp.example/site/siteA/prov/g1-record-derived",
                ontology_version=vocab.ONTOLOGY_VERSION,
            )
        )

        planned = assertion_store.query(modality=Modality.PLANNED)
        inferred = assertion_store.query(modality=Modality.INFERRED)
        assert len(planned) == 1
        assert len(inferred) == 1
        assert planned[0].graph != inferred[0].graph, "様相ごとに別のグラフに入る"
        assert inferred[0].confidence < 1.0, (
            "記録由来の信念は確実にならない（テンプレ入力の誤記率が効く）"
        )


class TestG1MappingContractsHold:
    """写像の 3 保存性が実データでも成り立つ（契約テストの実環境版）。"""

    async def test_all_four_sources_ingest_without_failure(
        self, pipeline: IngestionPipeline
    ) -> None:
        """4 つの業務情報源すべてが台本から取り込める。"""
        tables = ("work_order", "operation_log", "spare_part_stock", "maintenance_plan")
        total = 0
        for table in tables:
            with MockBusinessSystem(table=table) as mock:
                mock.apply(build_default_scenario(T0))
                rows = list(await mock.pull())
            result = pipeline.ingest_rows(rows)
            assert result.failed == [], f"{table}: 写像が失敗した {result.failed}"
            assert result.resolved_count == len(result.records), f"{table}: is_about が未解決"
            total += len(result.records)
        assert total == 4, "台本の 4 つの主張すべてが記録になる"

    async def test_ghost_inventory_record_is_ingested_as_a_claim(
        self, pipeline: IngestionPipeline
    ) -> None:
        """幽霊在庫（ERP は 3 個と言う）が「主張」として取り込まれる。

        物理が 0 個であることは M2 以降の観測が決める。M1 の時点では「ERP がそう言っている」
        という主張が正しく入ることまでを確認する。
        """
        with MockBusinessSystem(table="spare_part_stock") as mock:
            mock.apply(build_default_scenario(T0))
            rows = list(await mock.pull())
        result = pipeline.ingest_rows(rows)
        assert len(result.records) == 1
        record = result.records[0]
        assert record.payload["quantity"] == 3, "ERP の主張がそのまま保持される"
        assert record.external_id == "SKU-77@WH-A-01", "複合キーが external_id に写る"
        assert record.source == vocab.ent.ErpSource

    async def test_delayed_work_order_keeps_its_reporting_lag(
        self, pipeline: IngestionPipeline
    ) -> None:
        """遅延記帳（翌日入力）が時刻ずれとして保持される（類型 3 の材料）。"""
        with MockBusinessSystem(table="work_order") as mock:
            mock.apply(build_default_scenario(T0))
            rows = list(await mock.pull())
        record = pipeline.ingest_rows(rows).records[0]
        lag = record.reporting_lag
        assert lag is not None
        assert lag > timedelta(hours=12), f"遅延記帳が保持されていない: {lag}"

        reliability = ReliabilityRegistry.from_directory(REPO_ROOT / "reliability")
        model = reliability.for_record(record)
        bias = model.explains_as_systematic(record)
        assert bias is not None, "この遅れは月末一括計上の系統誤差で説明できる"
        assert bias.kind == "month_end_batch"


class TestG1WritebackStaysDry:
    """書き戻しは既定で業務システムを変更しない（設計書 §7.6）。"""

    def test_dry_run_does_not_modify_the_mock_business_system(self) -> None:

        with MockBusinessSystem(table="operation_log") as mock:
            mock.apply(build_default_scenario(T0))
            before = list(asyncio.run(mock.pull()))
            assert before[0].fields["running"] == 1

            executor = WritebackExecutor()  # 既定 dry
            outcome = executor.execute(
                WritebackCommand(
                    target=WritebackTarget(
                        source=vocab.ent.MesSource,
                        table="operation_log",
                        external_id=before[0].external_id,
                    ),
                    changes={"running": 0},
                    compensation="ent:RestoreOperationLog",
                    reason="物理観測が停止を示したため MES 実績の訂正を提案（設計書 §14）",
                )
            )
            assert outcome.result is WritebackResult.PLANNED

            after = list(asyncio.run(mock.pull()))
            assert after[0].fields["running"] == 1, "dry run で業務システムが変更された"


class TestG1StackReadiness:
    def test_ontology_version_matches_between_code_and_store(self, graph_store: object) -> None:
        """コードと知識グラフのオントロジー版が一致している。"""
        assert isinstance(graph_store, HttpGraphStore)
        rows = graph_store.query(
            "SELECT (COUNT(*) AS ?n) WHERE { GRAPH "
            f"<{schema_graph(vocab.ONTOLOGY_VERSION)}> {{ ?s ?p ?o }} }}"
        )
        assert int(rows[0]["n"] or "0") > 800, (
            f"オントロジー {vocab.ONTOLOGY_VERSION} が投入されていない。`make seed` を実行する"
        )
