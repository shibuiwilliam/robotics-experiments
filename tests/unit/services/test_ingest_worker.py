"""取込ワーカーの単体テスト（M8-25-5、ADR-0016）。

外界（業務システム）は SQLite のモックで、テストが台本を流し込む。ワーカーは production の
コードとして、台帳 → 解決器 → 業務記録（送信）→ 計画 の順に 1 周する。

重点:

1. **台帳が先** — 台帳を同期してから業務記録を取り込むので、新しい設備の記録も解決できる。
2. **主張イベントが出る** — MES の「稼働」がフルーエントの確率のイベントになる（所見 115）。
3. **2 周目は何も増えない** — カーソル（送信の後に進める）と差分で、同じ行を二度送らない。
4. **送信に失敗したらカーソルを進めない** — 次の周期で同じイベント（同じ ``event_id``）を送り直す。
5. **配線漏れを数える** — 渡されなかった依存は ``unwired`` に出る（所見 109 の形）。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from services.ingest_worker import IngestWorker

from sbp.core import vocab
from sbp.core.config import IntegrationSettings, Settings
from sbp.core.modality import Modality
from sbp.integration import (
    MappingRegistry,
    MasterDataSync,
    PlanSync,
    Rejection,
    ReliabilityRegistry,
)
from sbp.integration.mock import MockBusinessSystem
from sbp.integration.mock.business_system import build_default_scenario
from sbp.knowledge import AssertionStore, EmbeddedGraphStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.core.events import EventEnvelope
    from sbp.core.records import Record

REPO_ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
SITE = "siteA"
P101 = "https://sbp.example/site/siteA/asset/P-101"
TABLES = ("asset_register", "operation_log", "work_order", "spare_part_stock", "maintenance_plan")


class _Sink:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, EventEnvelope]] = []
        self.fail = fail

    async def __call__(self, topic: str, event: EventEnvelope) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.sent.append((topic, event))


class _Records:
    """記録の存在の投影（``RecordIndex.add`` の代わり）。"""

    def __init__(self) -> None:
        self.added: list[Record] = []

    async def __call__(self, records: Sequence[Record]) -> int:
        self.added.extend(records)
        return len(records)


class _Quarantine:
    """隔離キューの代わり（``QuarantineStore.add`` の呼び出しを数える）。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.rejected: list[Rejection] = []
        self.fail = fail

    async def __call__(self, rejection: Rejection) -> bool:
        if self.fail:
            raise ConnectionError("db down")
        self.rejected.append(rejection)
        return True


class _Cursors:
    def __init__(self) -> None:
        self.saved: dict[tuple[str, str], datetime] = {}

    async def load(self) -> dict[tuple[str, str], datetime]:
        return dict(self.saved)

    async def save(self, source: str, table: str, pulled_through: datetime) -> None:
        self.saved[(source, table)] = pulled_through


def _connectors(db: Path) -> dict[str, MockBusinessSystem]:
    """外界: 業務システムのモック（1 表 1 コネクタ、同じ SQLite ファイル）。"""
    connectors = {table: MockBusinessSystem(table=table, db_path=db) for table in TABLES}
    for connector in connectors.values():
        connector.apply(build_default_scenario(T0))
    return connectors


def _graph() -> EmbeddedGraphStore:
    store = EmbeddedGraphStore()
    for layer in ("core", "domain"):
        for ttl in sorted((REPO_ROOT / "ontology" / layer).glob("*.ttl")):
            store.load_turtle(ttl.read_text(encoding="utf-8"), graph="urn:sbp:test:ontology")
    return store


def _worker(
    tmp_path: Path,
    *,
    sink: _Sink | None = None,
    cursors: _Cursors | None = None,
    records: _Records | None = None,
    quarantine: _Quarantine | None = None,
) -> tuple[IngestWorker, _Sink, _Cursors, EmbeddedGraphStore]:
    graph = _graph()
    assertions = AssertionStore(graph, site_id=SITE)
    sink = sink or _Sink()
    cursors = cursors or _Cursors()
    settings = Settings(site_id=SITE, integration=IntegrationSettings(poll_period_s=1e-6))
    worker = IngestWorker(
        settings,
        connectors=_connectors(tmp_path / "mock.sqlite"),
        mappings=MappingRegistry.from_directory(REPO_ROOT / "mappings"),
        reliability=ReliabilityRegistry.from_directory(REPO_ROOT / "reliability"),
        sink=sink,
        master=MasterDataSync(assertions, graph, site_id=SITE),
        plans=PlanSync(assertions, site_id=SITE),
        cursors=cursors,
        records=records,
        quarantine=quarantine,
    )
    return worker, sink, cursors, graph


class TestOneCycle:
    @pytest.mark.asyncio
    async def test_the_register_comes_first_so_records_resolve(self, tmp_path: Path) -> None:
        worker, sink, _cursors, _graph = _worker(tmp_path)
        await worker.step()
        claims = [e for _t, e in sink.sent if str(e.type).endswith("#Running")]
        assert [e.subject for e in claims] == [P101], "台帳の P-101 に解決している"

    @pytest.mark.asyncio
    async def test_records_are_sent_to_the_business_topic(self, tmp_path: Path) -> None:
        worker, sink, _cursors, _graph = _worker(tmp_path)
        await worker.step()
        assert {topic for topic, _e in sink.sent} == {"siteA.integration.business"}
        # 業務記録 4 行の「存在」+ MES の Running の主張 1。台帳の行はイベントにしない
        assert len(sink.sent) == 5

    @pytest.mark.asyncio
    async def test_the_plan_becomes_planned(self, tmp_path: Path) -> None:
        worker, _sink, _cursors, graph = _worker(tmp_path)
        await worker.step()
        rows = AssertionStore(graph, site_id=SITE).query(modality=Modality.PLANNED)
        assert [(row.subject, row.predicate) for row in rows] == [(P101, vocab.ent.Running)]

    @pytest.mark.asyncio
    async def test_cursors_advance_to_the_newest_update(self, tmp_path: Path) -> None:
        worker, _sink, cursors, _graph = _worker(tmp_path)
        await worker.step()
        key = (str(vocab.ent.MesSource), "operation_log")
        assert cursors.saved[key] == T0 + timedelta(minutes=5)
        assert all(table != "asset_register" for _source, table in cursors.saved), (
            "台帳は毎周期全行を読むのでカーソルを持たない"
        )


class TestRecordIndex:
    """記録の存在の投影（ADR-0017）。送信の後に書く。"""

    @pytest.mark.asyncio
    async def test_sent_records_are_indexed_with_their_entity(self, tmp_path: Path) -> None:
        records = _Records()
        worker, _sink, _c, _g = _worker(tmp_path, records=records)
        await worker.step()
        assert records.added, "業務記録の存在が投影に渡る"
        assert P101 in {record.is_about for record in records.added}

    @pytest.mark.asyncio
    async def test_a_failed_send_indexes_nothing(self, tmp_path: Path) -> None:
        """送れなかった記録は「あった」ことにしない。"""
        records = _Records()
        worker, _s, _c, _g = _worker(tmp_path, sink=_Sink(fail=True), records=records)
        with pytest.raises(ConnectionError):
            await worker.step()
        assert records.added == []


def _insert(db: Path, sql: str, params: tuple[object, ...]) -> None:
    """外界の模擬: 業務システムに壊れた行を直接入れる。"""
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute(sql, params)


class TestQuarantine:
    """構造が壊れた入力は捨てずに隔離へ回す（ADR-0035）。"""

    def _ledger_with_unknown_type(self, tmp_path: Path) -> None:
        _insert(
            tmp_path / "mock.sqlite",
            "INSERT INTO asset_register (asset_tag, asset_type, part_of, recorded_at, updated_at) "
            "VALUES (?, ?, NULL, ?, ?)",
            ("X-1", "Gizmo", T0.isoformat(), T0.isoformat()),
        )

    @pytest.mark.asyncio
    async def test_a_ledger_row_with_an_unknown_type_is_quarantined_with_its_fields(
        self, tmp_path: Path
    ) -> None:
        quarantine = _Quarantine()
        worker, _s, _c, _g = _worker(tmp_path, quarantine=quarantine)
        self._ledger_with_unknown_type(tmp_path)
        await worker.step()
        assert [r.subject for r in quarantine.rejected] == ["X-1"]
        rejection = quarantine.rejected[0]
        assert rejection.table == "asset_register"
        assert "Gizmo" in rejection.reason
        assert rejection.payload["fields"]["asset_type"] == "Gizmo", "生の入力を捨てない"

    @pytest.mark.asyncio
    async def test_a_row_that_fails_the_mapping_is_quarantined_not_lost(
        self, tmp_path: Path
    ) -> None:
        quarantine = _Quarantine()
        worker, _s, cursors, _g = _worker(tmp_path, quarantine=quarantine)
        _insert(
            tmp_path / "mock.sqlite",
            "INSERT INTO operation_log (log_id, asset_tag, running, shift, logged_at, "
            "recorded_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("OL-BAD", "P-101", 1, "shift1", "not-a-time", "not-a-time", T0.isoformat()),
        )
        await worker.step()
        assert [r.subject for r in quarantine.rejected] == ["OL-BAD"]
        assert quarantine.rejected[0].table == "operation_log"
        assert (str(vocab.ent.MesSource), "operation_log") in cursors.saved, (
            "隔離に積んだ行はカーソルが進んでよい（捨てたのではなく、残してある）"
        )

    @pytest.mark.asyncio
    async def test_a_failing_quarantine_write_does_not_advance_the_cursor(
        self, tmp_path: Path
    ) -> None:
        """隔離に積めなかった行は、次の周期に読み直す（失われない）。"""
        cursors = _Cursors()
        worker, _s, _c, _g = _worker(tmp_path, cursors=cursors, quarantine=_Quarantine(fail=True))
        _insert(
            tmp_path / "mock.sqlite",
            "INSERT INTO operation_log (log_id, asset_tag, running, shift, logged_at, "
            "recorded_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("OL-BAD", "P-101", 1, "shift1", "not-a-time", "not-a-time", T0.isoformat()),
        )
        with pytest.raises(ConnectionError):
            await worker.step()
        assert (str(vocab.ent.MesSource), "operation_log") not in cursors.saved

    @pytest.mark.asyncio
    async def test_a_failing_ledger_quarantine_write_stops_the_cycle_but_retries(
        self, tmp_path: Path
    ) -> None:
        """台帳の経路で積めなければ周期が落ち、次の周期に同じ行を積み直せる。"""
        failing = _Quarantine(fail=True)
        worker, _s, _c, _g = _worker(tmp_path, quarantine=failing)
        self._ledger_with_unknown_type(tmp_path)
        with pytest.raises(ConnectionError):
            await worker.step()
        failing.fail = False
        await worker.step()
        assert [r.subject for r in failing.rejected] == ["X-1"]

    @pytest.mark.asyncio
    async def test_healthy_events_are_sent_even_when_a_row_is_quarantined(
        self, tmp_path: Path
    ) -> None:
        quarantine = _Quarantine()
        worker, sink, _c, _g = _worker(tmp_path, quarantine=quarantine)
        _insert(
            tmp_path / "mock.sqlite",
            "INSERT INTO operation_log (log_id, asset_tag, running, shift, logged_at, "
            "recorded_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("OL-BAD", "P-101", 1, "shift1", "not-a-time", "not-a-time", T0.isoformat()),
        )
        await worker.step()
        assert len(sink.sent) == 5, "壊れた 1 行の隣の健全な行は送られる"
        assert [r.subject for r in quarantine.rejected] == ["OL-BAD"]

    @pytest.mark.asyncio
    async def test_a_quarantine_failure_still_lets_healthy_events_go_first(
        self, tmp_path: Path
    ) -> None:
        worker, sink, cursors, _g = _worker(tmp_path, quarantine=_Quarantine(fail=True))
        _insert(
            tmp_path / "mock.sqlite",
            "INSERT INTO operation_log (log_id, asset_tag, running, shift, logged_at, "
            "recorded_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("OL-BAD", "P-101", 1, "shift1", "not-a-time", "not-a-time", T0.isoformat()),
        )
        with pytest.raises(ConnectionError):
            await worker.step()
        assert sink.sent, "健全な行は隔離の失敗に巻き込まれない（送信は冪等）"
        assert (str(vocab.ent.MesSource), "operation_log") not in cursors.saved

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("column", "value"), [("asset_type", "Gizmo"), ("recorded_at", "not-a-time")]
    )
    async def test_a_broken_edit_does_not_retract_the_assets_last_valid_statement(
        self, tmp_path: Path, column: str, value: str
    ) -> None:
        """壊れた編集（型を未知の値にした）で、直前の有効な文を失わない。"""
        quarantine = _Quarantine()
        worker, _s, _c, graph = _worker(tmp_path, quarantine=quarantine)
        await worker.step()
        store = AssertionStore(graph, site_id=SITE)
        before = [r for r in store.query(subject=P101) if r.predicate.endswith("type")]
        assert before
        _insert(
            tmp_path / "mock.sqlite",
            f"UPDATE asset_register SET {column} = ?, updated_at = ? WHERE asset_tag = ?",
            (value, (T0 + timedelta(hours=1)).isoformat(), "P-101"),
        )
        await worker.step()
        after = [r for r in store.query(subject=P101) if r.predicate.endswith("type")]
        assert len(after) == len(before), "未知の型の行でも、直前の型の文は閉じない"
        assert [r.subject for r in quarantine.rejected] == ["P-101"]

    @pytest.mark.asyncio
    async def test_healthy_rows_are_not_quarantined(self, tmp_path: Path) -> None:
        quarantine = _Quarantine()
        worker, _s, _c, _g = _worker(tmp_path, quarantine=quarantine)
        await worker.step()
        assert quarantine.rejected == []


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_the_second_cycle_adds_nothing(self, tmp_path: Path) -> None:
        worker, sink, _cursors, graph = _worker(tmp_path)
        assert await worker.step() > 0
        sent = len(sink.sent)
        assert await worker.step() == 0
        assert len(sink.sent) == sent
        assert len(AssertionStore(graph, site_id=SITE).query(modality=Modality.PLANNED)) == 1

    @pytest.mark.asyncio
    async def test_a_failed_send_does_not_advance_the_cursor(self, tmp_path: Path) -> None:
        """送信に失敗した行は次の周期で同じ ``event_id`` のまま送り直す（所見 52）。"""
        cursors = _Cursors()
        failing, _sink, _c, _g = _worker(tmp_path, sink=_Sink(fail=True), cursors=cursors)
        with pytest.raises(ConnectionError):
            await failing.step()
        assert (str(vocab.ent.MesSource), "operation_log") not in cursors.saved

    @pytest.mark.asyncio
    async def test_a_restarted_worker_resumes_from_the_saved_cursor(self, tmp_path: Path) -> None:
        cursors = _Cursors()
        first, _sink, _c, _g = _worker(tmp_path, cursors=cursors)
        await first.step()
        restarted, sink, _c, _g = _worker(tmp_path, cursors=cursors)
        await restarted.step()
        assert sink.sent == [], "カーソルを読み戻すので、送った行を読み直さない"


class TestUnconfiguredPaths:
    @pytest.mark.asyncio
    async def test_unwired_dependencies_are_reported(self) -> None:
        worker = IngestWorker(
            Settings(),
            connectors={},
            mappings=MappingRegistry.from_directory(REPO_ROOT / "mappings"),
            reliability=ReliabilityRegistry.from_directory(REPO_ROOT / "reliability"),
        )
        assert worker.unwired == (
            "connectors",
            "sink",
            "master",
            "plans",
            "cursors",
            "records",
            "quarantine",
        )
        assert await worker.step() == 0

    @pytest.mark.asyncio
    async def test_the_poll_period_is_respected(self, tmp_path: Path) -> None:
        worker, sink, _cursors, _graph = _worker(tmp_path)
        worker.settings = Settings(site_id=SITE)  # 既定の 60 秒
        await worker.step()
        sent = len(sink.sent)
        assert await worker.step() == 0
        assert len(sink.sent) == sent
