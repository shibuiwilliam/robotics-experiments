"""取込ワーカー（6 番目のワーカー。M8-25-5、ADR-0016）。

設計書 §2.5 の**記録パス**（業務 → 信念）を運用で回す。周期ごとに次の順で 1 周する:

1. **資産台帳**を全行読み、設備の型と部分−全体を observed のアサーションと投影グラフに同期する
   （``MasterDataSync``）。台帳に載った資産タグを解決器に登録する。
2. **業務記録**を前回のカーソルから差分で読み、写像 → ``is_about`` 解決 → 「存在する」イベントと
   主張イベント（``…/ent:Running`` など）にして ``<site>.integration.business`` へ送る。
3. **計画**（``maintenance_plan``）を planned の文として同期する（``PlanSync``）。

**台帳を先にする。** 後にすると、新しい設備の記録が ``UnresolvedRef`` になる。

**カーソルは送信の後に進める**（所見 52）。送信に失敗した行は次の周期で読み直し、決定的な
``event_id``（``stable_event_id``）なので消費側の冪等記録が重複を弾く。

**このモジュールは配線だけを持つ。** 写像・主張・信頼性は ``sbp.integration``、台帳と計画の
永続化は ``sbp.knowledge`` の公開 API にある（ワーカーにロジックを書くと、テストが Compose 必須に
なる）。

外界（業務システム）は開発ではファイルの SQLite のモック（``SBP_INTEGRATION_MOCK_DB_PATH``）。
実システムのコネクタは本リポジトリでは扱わない（ADR-0016 の未決）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc, now
from sbp.integration import (
    IngestionPipeline,
    MasterDataResolver,
    intended_fluent_for,
    plan_item_from_record,
)
from services.runtime import ReadinessCheck, WorkerRuntime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from sbp.core.events import EventEnvelope
    from sbp.core.records import Record
    from sbp.eventlog.producer import KafkaEventProducer
    from sbp.integration import (
        Connector,
        IngestResult,
        MappingRegistry,
        MasterDataSync,
        PlanSync,
        Rejection,
        ReliabilityRegistry,
    )
    from sbp.integration.connectors import RawRecord
    from services.metrics import MetricRecorder

    EventSink = Callable[[str, EventEnvelope], Awaitable[object]]
    """``(トピック, イベント)`` を送る（``KafkaEventProducer.send`` を包む）。"""
    RecordSink = Callable[[Sequence[Record]], Awaitable[object]]
    """送信し終えた記録の存在を投影表に追記する（``RecordIndex.add``。ADR-0017）。"""
    QuarantineSink = Callable[[Rejection], Awaitable[object]]
    """構造の理由で受け付けなかった入力を隔離キューに積む（``QuarantineStore.add``。ADR-0035）。"""

__all__ = ["MASTER_TABLE", "PLAN_TABLE", "IngestWorker", "build_worker", "main"]

log = get_logger(__name__)

WORKER_NAME = "ingest-worker"

MASTER_TABLE = "asset_register"
"""資産台帳の表。毎周期全行を読む（消えた行を知るため）。"""

PLAN_TABLE = "maintenance_plan"
"""計画の表。業務記録としても送り、planned の文としても同期する。"""

TABLES = (MASTER_TABLE, "operation_log", "work_order", "spare_part_stock", PLAN_TABLE)
"""開発用モックから読む表（台帳が先頭）。"""


class CursorStore(Protocol):
    """取込カーソルの読み書き（``sbp.knowledge.IngestCursorStore`` が満たす）。"""

    async def load(self) -> dict[tuple[str, str], datetime]:
        """``(情報源, 表) → 読み終えた updated_at``。"""
        ...

    async def save(self, source: str, table: str, pulled_through: datetime) -> None:
        """1 表ぶんを書く（後退させない）。"""
        ...


def readiness_checks(settings: Settings) -> tuple[ReadinessCheck, ...]:
    """このワーカーの依存（イベントログと知識グラフ）。

    Postgres（カーソル）は ``build_worker`` が構築時に接続するので、ここでは検査しない。

    Args:
        settings: 設定。
    """
    from services.belief_worker import (  # noqa: PLC0415 — 疎通検査を共有する
        http_reachable,
        tcp_reachable,
    )

    return (
        ReadinessCheck(name="redpanda", probe=lambda: tcp_reachable(settings.kafka_bootstrap)),
        ReadinessCheck(name="oxigraph", probe=lambda: http_reachable(settings.sparql_query_url)),
    )


class IngestWorker:
    """業務システムを周期で読み、台帳・業務記録・計画を取り込む。

    Attributes:
        settings: 設定。
        runtime: 実行ループ。
        unwired: 渡されなかった依存の名前。落とさずに数えて警告する（所見 109 の形）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        connectors: Mapping[str, Connector],
        mappings: MappingRegistry,
        reliability: ReliabilityRegistry,
        sink: EventSink | None = None,
        master: MasterDataSync | None = None,
        plans: PlanSync | None = None,
        cursors: CursorStore | None = None,
        metrics: MetricRecorder | None = None,
        records: RecordSink | None = None,
        quarantine: QuarantineSink | None = None,
    ) -> None:
        """Args:
        settings: 設定（既定は環境から読む）。
        connectors: 表名 → コネクタ（1 表 1 コネクタ）。
        mappings: 写像レジストリ。
        reliability: 信頼性モデルのレジストリ。
        sink: イベントの送信先（``None`` なら送らない。取り込みは進めない）。
        master: 資産台帳の同期（``None`` なら台帳を同期しない）。
        plans: 計画の同期（``None`` なら planned の文を書かない）。
        cursors: 取込カーソル（``None`` なら毎周期全行を読む）。
        metrics: 指標の記録先（``cdc_lag`` / ``mapping_violations``）。
        records: 記録の存在の投影（``None`` なら書かない。期待の記号側の検証が記録を引けない）。
        quarantine: 受け付けなかった入力の隔離先（``None`` ならログだけで人の目に触れない）。
        """
        self.settings = settings or Settings()
        self.runtime = WorkerRuntime(
            name=WORKER_NAME, settings=self.settings, checks=readiness_checks(self.settings)
        )
        self._connectors = dict(connectors)
        self._mappings = mappings
        self._resolver = MasterDataResolver(site_id=self.settings.site_id)
        self._pipeline = IngestionPipeline(
            site_id=self.settings.site_id,
            mappings=mappings,
            resolver=self._resolver,
            reliability=reliability,
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        self._sink = sink
        self._master = master
        self._plans = plans
        self._cursor_store = cursors
        self._records = records
        self._quarantine = quarantine
        self._cursors: dict[tuple[str, str], datetime] | None = None
        self._metrics = metrics
        self._last_poll: datetime | None = None
        self.unwired = tuple(
            name
            for name, given in (
                ("connectors", self._connectors or None),
                ("sink", sink),
                ("master", master),
                ("plans", plans),
                ("cursors", cursors),
                ("records", records),
                ("quarantine", quarantine),
            )
            if given is None
        )
        if self.unwired:
            log.warning(
                "worker.ingest_not_wired",
                worker=WORKER_NAME,
                missing=list(self.unwired),
                detail="渡されなかった段は動かない（業務記録・台帳・計画のどれかが知識に届かない）",
            )

    def poll_due(self, *, at: datetime) -> bool:
        """この時点で業務システムを読みに行くべきか。

        Args:
            at: 判定時刻。
        """
        if self._last_poll is None:
            return True
        return (at - self._last_poll).total_seconds() >= self.settings.integration.poll_period_s

    async def step(self) -> int:
        """1 周。台帳 → 解決器 → 業務記録（送信）→ 計画。

        Returns:
            この周期で行ったこと（送ったイベント + 台帳の文の追記・閉鎖 + 計画の追記・置き換え）
            の数。
        """
        at = now()
        if not self._connectors or not self.poll_due(at=at):
            return 0
        self._last_poll = at
        if self._cursors is None:
            self._cursors = {} if self._cursor_store is None else await self._cursor_store.load()

        handled = await self._sync_master(at=at)
        for table, connector in self._connectors.items():
            if table != MASTER_TABLE:
                handled += await self._ingest_table(table, connector, at=at)
        if self._metrics is not None:
            await self._metrics.flush(at=at)
        if handled:
            log.info("worker.ingest_cycle", worker=WORKER_NAME, handled=handled)
        return handled

    async def _sync_master(self, *, at: datetime) -> int:
        """資産台帳を全行同期し、資産タグを解決器に登録する。"""
        connector = self._connectors.get(MASTER_TABLE)
        if connector is None or self._master is None:
            return 0
        rows = list(await connector.pull(None))
        records, rejected = self._map(rows, table=MASTER_TABLE)
        report = await asyncio.to_thread(self._master.sync, records, at=at)
        await self._quarantine_all([*rejected, *report.rejected])
        for tag in sorted(report.entities):
            self._resolver.register("asset", tag)
        return report.asserted + report.retracted

    async def _ingest_table(self, table: str, connector: Connector, *, at: datetime) -> int:
        """業務記録の表を差分で取り込み、送信してからカーソルを進める。"""
        assert self._cursors is not None
        key = (connector.source, table)
        rows = list(await connector.pull(self._cursors.get(key)))
        if not rows:
            return 0
        result = self._pipeline.ingest_rows(rows)
        self._measure(result, table=table, at=at)
        # カーソルを進める前に隔離へ積む。積めなければ例外でカーソルは進まず、次周期に読み直す
        await self._quarantine_all(result.rejected)
        if self._sink is None:
            # 送れないならカーソルも進めない（取り込んだことにしない）
            return 0
        for topic, event in result.events:
            await self._sink(topic, event)
        handled = len(result.events)
        if self._records is not None:
            # 送信の後・カーソルの前。書けなければカーソルは進まず、次周期に送り直す（冪等）
            await self._records(result.records)
        if table == PLAN_TABLE:
            handled += await self._sync_plans(result.records, at=at)
        newest = _newest_revision(rows)
        if newest is not None:
            self._cursors[key] = newest
            if self._cursor_store is not None:
                await self._cursor_store.save(connector.source, table, newest)
        return handled

    async def _sync_plans(self, records: Sequence[Record], *, at: datetime) -> int:
        """計画を planned の文として同期する。対象が未解決・計画種が未知の計画は飛ばす。"""
        if self._plans is None:
            return 0
        changed = 0
        for record in records:
            fluent = intended_fluent_for(record)
            if fluent is None or not record.is_resolved:
                if not record.is_resolved:
                    log.info("worker.plan_unresolved", external_id=record.external_id)
                continue
            plan = plan_item_from_record(record, intended_fluent=fluent)
            outcome = await asyncio.to_thread(self._plans.sync, plan, at=at)
            changed += outcome != "unchanged"
        return changed

    def _map(
        self, rows: Sequence[RawRecord], *, table: str
    ) -> tuple[list[Record], list[Rejection]]:
        """台帳の行を写像に通す。失敗した行は生の入力ごと返す（隔離キューへ渡す）。"""
        mapping = self._mappings.for_table(table)
        records: list[Record] = []
        rejected: list[Rejection] = []
        for raw in rows:
            try:
                records.append(mapping.apply(raw, ontology_version=vocab.ONTOLOGY_VERSION).record)
            except Exception as exc:
                log.warning("worker.master_row_failed", external_id=raw.external_id, error=str(exc))
                rejected.append(
                    Rejection(
                        source=raw.source,
                        table=raw.table,
                        subject=raw.external_id,
                        reason=f"mapping failed: {exc}",
                        payload={"fields": dict(raw.fields)},
                    )
                )
        return records, rejected

    async def _quarantine_all(self, rejections: Sequence[Rejection]) -> None:
        """受け付けなかった入力を隔離キューに積む（冪等。積めなければ例外のまま上げる）。"""
        if self._quarantine is None:
            return
        for rejection in rejections:
            await self._quarantine(rejection)

    def _measure(self, result: IngestResult, *, table: str, at: datetime) -> None:
        """取込 1 回ぶんの指標（``cdc_lag``・``mapping_violations``）を記録する。"""
        if self._metrics is None:
            return
        labels = {"table": table}
        self._metrics.record("mapping_violations", float(len(result.failed)), at=at, labels=labels)
        if result.records:
            newest = max(record.recorded_at for record in result.records)
            lag = max(0.0, (at - newest).total_seconds())
            self._metrics.record("cdc_lag", lag, at=at, labels=labels)

    async def run(self, *, max_iterations: int | None = None) -> int:
        """起動する。

        Args:
            max_iterations: テスト用の上限。
        """
        loop = asyncio.get_running_loop()
        self.runtime.install_signal_handlers(loop)
        return await self.runtime.run(self.step, max_iterations=max_iterations)


def _newest_revision(rows: Sequence[RawRecord]) -> datetime | None:
    """読んだ行の ``updated_at`` の最大。1 行でも無い・読めないなら ``None``（カーソルを進めない）。

    進めないと次の周期も全行を読むが、決定的な ``event_id`` で消費側が弾くので壊れない。推測で
    進めるほうが危ない（読んでいない行を読んだことにする）。
    """
    revisions: list[datetime] = []
    for raw in rows:
        value = raw.fields.get("updated_at")
        if not isinstance(value, str):
            return None
        try:
            revisions.append(ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00"))))
        except ValueError:
            return None
    return max(revisions) if revisions else None


async def build_worker(
    settings: Settings | None = None, *, site_id: str
) -> tuple[IngestWorker, KafkaEventProducer]:
    """実運用の配線を組む（業務システム → Kafka・知識グラフ・Postgres）。

    Args:
        settings: 設定（既定は環境から読む）。
        site_id: 拠点識別子。

    Returns:
        ``(ワーカー, producer)``。呼び出し側が producer の停止を担う。
    """
    import psycopg  # noqa: PLC0415 — 実運用の配線でのみ使う

    from sbp.eventlog.producer import KafkaEventProducer  # noqa: PLC0415
    from sbp.integration import (  # noqa: PLC0415
        MappingRegistry,
        MasterDataSync,
        PlanSync,
        ReliabilityRegistry,
    )
    from sbp.integration.mock import MockBusinessSystem  # noqa: PLC0415
    from sbp.knowledge import (  # noqa: PLC0415
        AssertionStore,
        HttpGraphStore,
        IngestCursorStore,
        MetricStore,
        QuarantineStore,
        RecordIndex,
    )
    from services.metrics import MetricRecorder  # noqa: PLC0415

    resolved = (settings or Settings()).model_copy(update={"site_id": site_id})
    graph = HttpGraphStore(resolved)
    assertions = AssertionStore(graph, site_id=site_id)
    connection = await psycopg.AsyncConnection.connect(resolved.pg_dsn, autocommit=True)
    producer = KafkaEventProducer(resolved)
    await producer.start()

    db_path = resolved.integration.mock_db_path
    connectors: dict[str, Connector] = (
        {table: MockBusinessSystem(table=table, db_path=Path(db_path)) for table in TABLES}
        if db_path
        else {}
    )

    async def sink(topic: str, event: EventEnvelope) -> object:
        """送ってから戻る（送信の後にカーソルを進めるため、待つ）。"""
        return await producer.send(topic, event)

    quarantine_store = QuarantineStore(connection, site=site_id)

    async def quarantine(rejection: Rejection) -> object:
        """受け付けなかった入力を隔離に積む（同じ入力・同じ理由は 1 行）。"""
        return await quarantine_store.add(
            source=rejection.source,
            table=rejection.table,
            subject=rejection.subject,
            reason=rejection.reason,
            payload=rejection.payload,
        )

    worker = IngestWorker(
        resolved,
        connectors=connectors,
        mappings=MappingRegistry.from_directory(Path("mappings")),
        reliability=ReliabilityRegistry.from_directory(Path("reliability")),
        sink=sink,
        master=MasterDataSync(assertions, graph, site_id=site_id),
        plans=PlanSync(assertions, site_id=site_id),
        cursors=IngestCursorStore(connection, site=site_id),
        metrics=MetricRecorder(site=site_id, sink=MetricStore(connection, site=site_id).write),
        records=RecordIndex(connection, site=site_id).add,
        quarantine=quarantine,
    )
    log.info("worker.wired", worker=WORKER_NAME, site_id=site_id, tables=sorted(connectors))
    return worker, producer


def main() -> int:
    """エントリポイント。

    業務システムは ``SBP_INTEGRATION_MOCK_DB_PATH`` の SQLite のモック（開発用）。空なら
    コネクタ無しで起動し、``unwired`` で報告する。
    """

    async def _run() -> None:
        settings = Settings()
        worker, producer = await build_worker(settings, site_id=settings.site_id)
        try:
            await worker.run()
        finally:
            await producer.stop()

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
