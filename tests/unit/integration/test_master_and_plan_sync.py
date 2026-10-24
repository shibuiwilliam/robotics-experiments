"""資産台帳の同期と計画の planned 化の単体テスト（M8-25-4、ADR-0016）。

組み込み pyoxigraph を使う（サーバ不要）。台帳は**アサーション + 投影**で持つ:
台帳の各行を observed の二時制アサーションとして追記し、現在有効な文から素の三つ組の投影グラフ
``urn:sbp:g:master:<site>`` を作り直す。読み手（``entities_of_type`` / ``part_whole_edges``）は
投影を読む。

重点:

1. **投影が読み手に効く** — 同期の後、接地対象（``entities_of_type``）と部分−全体
   （``part_whole_edges``）が台帳の設備を返す。
2. **追記専用** — 台帳から消えた・変わった行は ``tx_to`` を閉じるだけで消さない。as-of で旧い台帳が
   読み戻せる（不変条件 2）。
3. **2 回目は何も増えない** — 同じ台帳を同期し直しても文は増えない（取込は周期実行される）。
4. **推測しない** — オントロジーに無い型の行は登録せず、理由を返す。計画も、知らない計画種の
   フルーエントを推測しない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sbp.core import vocab
from sbp.core.modality import Modality
from sbp.core.records import Record
from sbp.integration.master import MasterDataSync
from sbp.integration.plans import PlanSync, intended_fluent_for, plan_item_from_record
from sbp.knowledge import AssertionStore, EmbeddedGraphStore, entities_of_type, part_whole_edges
from sbp.knowledge.graphs import master_graph

REPO_ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
SITE = "siteA"
LINE = "https://sbp.example/site/siteA/asset/LINE-3"
P101 = "https://sbp.example/site/siteA/asset/P-101"
P102 = "https://sbp.example/site/siteA/asset/P-102"


def _register_row(tag: str, asset_type: str, part_of: str | None = None) -> Record:
    return Record(
        record_type=vocab.ent.AssetRegisterEntry,
        source=vocab.ent.CmmsSource,
        external_id=tag,
        recorded_at=T0 - timedelta(days=30),
        payload={"asset_type": asset_type, "part_of": part_of},
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


def _register() -> list[Record]:
    return [
        _register_row("LINE-3", "ProductionLine"),
        _register_row("P-101", "Pump", "LINE-3"),
        _register_row("P-102", "Pump", "LINE-3"),
    ]


@pytest.fixture
def graph() -> EmbeddedGraphStore:
    """オントロジー（型の階層）を入れたストア。実運用でも ``make seed`` が先に入れる。"""
    store = EmbeddedGraphStore()
    for layer in ("core", "domain"):
        for ttl in sorted((REPO_ROOT / "ontology" / layer).glob("*.ttl")):
            store.load_turtle(ttl.read_text(encoding="utf-8"), graph="urn:sbp:test:ontology")
    return store


@pytest.fixture
def sync(graph: EmbeddedGraphStore) -> MasterDataSync:
    return MasterDataSync(AssertionStore(graph, site_id=SITE), graph, site_id=SITE)


class TestMasterDataSync:
    def test_the_projection_feeds_the_grounding_targets(
        self, graph: EmbeddedGraphStore, sync: MasterDataSync
    ) -> None:
        sync.sync(_register(), at=T0)
        assert entities_of_type(graph, types=(vocab.core.Asset,), site=SITE) == [P101, P102]

    def test_the_projection_feeds_the_part_whole_coupling(
        self, graph: EmbeddedGraphStore, sync: MasterDataSync
    ) -> None:
        sync.sync(_register(), at=T0)
        assert sorted(part_whole_edges(graph)) == [(P101, LINE), (P102, LINE)]

    def test_the_register_is_kept_as_observed_assertions(
        self, graph: EmbeddedGraphStore, sync: MasterDataSync
    ) -> None:
        """投影は文から作り直せる派生物で、記録として残るのはアサーションのほう。"""
        report = sync.sync(_register(), at=T0)
        assert report.asserted == 5, "型 3 + 部分−全体 2"
        rows = AssertionStore(graph, site_id=SITE).query(modality=Modality.OBSERVED)
        assert len(rows) == 5
        assert {row.provenance for row in rows} == {sync.provenance}

    def test_syncing_the_same_register_again_adds_nothing(self, sync: MasterDataSync) -> None:
        sync.sync(_register(), at=T0)
        report = sync.sync(_register(), at=T0 + timedelta(minutes=1))
        assert (report.asserted, report.retracted, report.unchanged) == (0, 0, 5)

    def test_a_removed_asset_is_closed_not_deleted(
        self, graph: EmbeddedGraphStore, sync: MasterDataSync
    ) -> None:
        """台帳から消えた設備は接地対象から外れるが、as-of では旧い台帳が読める（不変条件 2）。"""
        sync.sync(_register(), at=T0)
        report = sync.sync(_register()[:2], at=T0 + timedelta(hours=1))
        assert report.retracted == 2, "P-102 の型と部分−全体"
        assert entities_of_type(graph, types=(vocab.core.Asset,), site=SITE) == [P101]
        before = AssertionStore(graph, site_id=SITE).query(
            subject=P102, as_of=T0 + timedelta(minutes=30)
        )
        assert len(before) == 2

    def test_a_changed_type_replaces_the_old_statement(
        self, graph: EmbeddedGraphStore, sync: MasterDataSync
    ) -> None:
        sync.sync(_register(), at=T0)
        changed = [*_register()[:2], _register_row("P-102", "RotatingEquipment", "LINE-3")]
        report = sync.sync(changed, at=T0 + timedelta(hours=1))
        assert (report.asserted, report.retracted) == (1, 1)
        current = AssertionStore(graph, site_id=SITE).query(subject=P102)
        assert {row.object_ for row in current} == {vocab.ent.RotatingEquipment, LINE}

    def test_an_unknown_type_is_not_guessed(self, sync: MasterDataSync) -> None:
        """綴り違いの型を登録すると、どの型の問い合わせにも当たらない設備が静かにできる。"""
        report = sync.sync([*_register(), _register_row("X-1", "Pumpp")], at=T0)
        assert [external_id for external_id, _reason in report.skipped] == ["X-1"]
        assert report.entities == frozenset({"LINE-3", "P-101", "P-102"})

    def test_the_projection_lives_in_the_site_master_graph(
        self, graph: EmbeddedGraphStore, sync: MasterDataSync
    ) -> None:
        sync.sync(_register(), at=T0)
        rows = graph.query(
            f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{master_graph(SITE)}> {{ ?s ?p ?o }} }}"
        )
        assert rows[0]["n"] == "5"


def _plan_record(**payload: Any) -> Record:
    defaults: dict[str, Any] = {
        "plan_type": "PlannedOperation",
        "window_start": "2026-09-12T09:00:00Z",
        "window_end": "2026-09-12T17:00:00Z",
    }
    return Record(
        record_type=vocab.ent.MaintenancePlan,
        source=vocab.ent.CmmsSource,
        external_id="MP-0912",
        is_about=P101,
        recorded_at=T0 - timedelta(days=7),
        payload=defaults | payload,
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


class TestPlanSync:
    @pytest.mark.parametrize(
        ("plan_type", "fluent"),
        [
            ("PlannedOperation", vocab.ent.Running),
            ("PreventiveMaintenance", vocab.ent.UnderMaintenance),
            ("Inspection", vocab.ent.Inspected),
        ],
    )
    def test_the_plan_type_decides_the_fluent(self, plan_type: str, fluent: str) -> None:
        assert intended_fluent_for(_plan_record(plan_type=plan_type)) == fluent

    def test_an_unknown_plan_type_is_not_guessed(self) -> None:
        assert intended_fluent_for(_plan_record(plan_type="SomethingNew")) is None

    def _sync(self, graph: EmbeddedGraphStore, record: Record, at: datetime) -> str:
        fluent = intended_fluent_for(record)
        assert fluent is not None
        plan = plan_item_from_record(record, intended_fluent=fluent)
        return PlanSync(AssertionStore(graph, site_id=SITE), site_id=SITE).sync(plan, at=at)

    def test_a_plan_becomes_one_planned_assertion(self, graph: EmbeddedGraphStore) -> None:
        assert self._sync(graph, _plan_record(), T0) == "asserted"
        rows = AssertionStore(graph, site_id=SITE).query(modality=Modality.PLANNED)
        assert [(row.subject, row.predicate) for row in rows] == [(P101, vocab.ent.Running)]

    def test_syncing_the_same_plan_again_adds_nothing(self, graph: EmbeddedGraphStore) -> None:
        self._sync(graph, _plan_record(), T0)
        assert self._sync(graph, _plan_record(), T0 + timedelta(minutes=1)) == "unchanged"
        assert len(AssertionStore(graph, site_id=SITE).query(modality=Modality.PLANNED)) == 1

    def test_a_moved_window_replaces_the_old_plan(self, graph: EmbeddedGraphStore) -> None:
        """計画の変更は旧文の ``tx_to`` を閉じて新文を足す（上書きしない）。"""
        self._sync(graph, _plan_record(), T0)
        moved = _plan_record(window_end="2026-09-12T13:00:00Z")
        assert self._sync(graph, moved, T0 + timedelta(hours=1)) == "replaced"
        store = AssertionStore(graph, site_id=SITE)
        current = store.query(modality=Modality.PLANNED)
        assert [row.valid_to for row in current] == [datetime(2026, 9, 12, 13, tzinfo=UTC)]
        history = store.query(modality=Modality.PLANNED, include_closed=True)
        assert len(history) == 2
