"""信念更新ワーカー（M5-08）。

プローブ由来のイベント・業務記録・計画を読み、実体単位の前向きフィルタを進めて ``inferred``
アサーションを書く。実体間結合（loopy BP）は ``bp_period_s`` ごとに回す。

**結合を毎ループ回さない。** 実体間のメッセージパッシングは辺数に比例して重く、1 件のイベント
ごとに回すと処理が追いつかない。設計書 §8.1 が「一定周期で回す」と言っているのはこのためで、
周期は ``Settings.reasoning.bp_period_s``。

このモジュールは**配線だけ**を持つ。信念更新の中身は ``sbp.reasoning.belief``、イベントから
信念への変換は ``sbp.reasoning.ingest``、結合は ``sbp.reasoning.coupling`` にある
（ワーカーにロジックを書くと、テストが Compose 必須になる）。

**イベントの取得を「driver」として受け取る。** 当初はポーリング（``(件数) -> イベント列``）に
していたが、それは冪等性を壊す。``sbp.eventlog.process_once`` は**副作用の前に**冪等記録を
付けるので、ハンドラが「バッファに溜めるだけ」だと、溜めた後に落ちたイベントは
「処理済みなのに信念に入っていない」状態で永久に失われる。

そのため**ハンドラ自身が本体の仕事（信念更新と書き込み）をする**形にし、ワーカーは
「このハンドラで最大 N 件処理してくれ」と driver に頼むだけにした。単体テストは一覧から
ハンドラを呼ぶ driver を渡し、実運用は ``KafkaEventConsumer`` を渡す——**ロジックのある部分
（ハンドラ）は両方で同じものが動く**。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.logging import get_logger
from sbp.core.time import now
from sbp.reasoning.ingest import BeliefIngest, default_transition
from services.runtime import ReadinessCheck, WorkerRuntime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from sbp.core.assertion import Assertion
    from sbp.core.events import EventEnvelope
    from sbp.eventlog.consumer import EventHandler, KafkaEventConsumer

    EventDriver = Callable[["EventHandler", int], Awaitable[int]]
    """``(ハンドラ, 最大件数) -> 処理した件数``。

    実運用では ``KafkaEventConsumer.run(max_events=...)`` を包む。単体テストでは一覧から
    ハンドラを呼ぶ。**ハンドラを渡す形にしてあるのが要点である**——冪等記録は副作用の前に
    付くので、ハンドラが本体の仕事をしないと at-least-once が壊れる。
    """
    AssertionSink = Callable[[Assertion], object]
    """アサーションを 1 件書く。戻り値は使わない（``assert_`` は ID を返す）。"""
    CommitHook = Callable[[], Awaitable[None]]
    """終了時のオフセットコミット。"""

log = get_logger(__name__)

WORKER_NAME = "belief-worker"


def default_ingest(settings: Settings) -> BeliefIngest:
    """``vocab`` の述語表から配線を作る。

    **述語 IRI は ``vocab`` から取る**（不変条件 5: IRI をコードに直書きしない）。
    フルーエントのローカル名が ``vocab.ent`` の属性名と一致していることが前提で、これは
    CLAUDE.md の命名規約（フルーエント・事象型はオントロジーのローカル名と一致させる）である。

    Args:
        settings: 設定（刻み幅に使う）。
    """
    predicates = {
        name: str(getattr(vocab.ent, name)) for name in dir(vocab.ent) if not name.startswith("_")
    }
    return BeliefIngest(
        provenance="urn:sbp:prov:belief-worker",
        ontology_version=vocab.ONTOLOGY_VERSION,
        predicate_of=predicates,
        transition=default_transition(step=timedelta(seconds=settings.reasoning.filter_step_s)),
    )


def readiness_checks(settings: Settings) -> tuple[ReadinessCheck, ...]:
    """このワーカーの依存（イベントログと知識グラフ）。

    Args:
        settings: 設定。
    """
    return (
        ReadinessCheck(name="redpanda", probe=lambda: _tcp_reachable(settings.kafka_bootstrap)),
        ReadinessCheck(name="oxigraph", probe=lambda: _http_reachable(settings.sparql_query_url)),
    )


def _tcp_reachable(bootstrap: str) -> bool:
    """``host:port`` に TCP で到達できるか。"""
    import socket  # noqa: PLC0415 — 疎通検査だけに使う

    host, _, port = bootstrap.partition(":")
    try:
        with socket.create_connection((host or "localhost", int(port or 9092)), timeout=2.0):
            return True
    except OSError:
        return False


def _http_reachable(url: str) -> bool:
    """HTTP エンドポイントに到達できるか。"""
    import httpx  # noqa: PLC0415 — 疎通検査だけに使う

    try:
        response = httpx.get(url, params={"query": "ASK {}"}, timeout=2.0)
    except httpx.HTTPError:
        return False
    return response.status_code < 500


class BeliefWorker:
    """信念更新と周期的な実体間結合。

    Attributes:
        settings: 設定。
        runtime: 実行ループ。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        driver: EventDriver | None = None,
        sink: AssertionSink | None = None,
        ingest: BeliefIngest | None = None,
    ) -> None:
        """Args:
        settings: 設定（既定は環境から読む）。
        driver: ``(ハンドラ, 最大件数) -> 処理件数``（``None`` なら取り込まない。
            設定漏れは起動時に警告する）。
        sink: アサーションの書き込み（``None`` なら書かない）。
        ingest: 信念更新の配線（既定は ``vocab`` の述語表で作る）。
        """
        self.settings = settings or Settings()
        self.runtime = WorkerRuntime(
            name=WORKER_NAME,
            settings=self.settings,
            checks=readiness_checks(self.settings),
        )
        self._last_coupling: datetime | None = None
        self._driver = driver
        self._sink = sink
        self.ingest = ingest if ingest is not None else default_ingest(self.settings)
        self.applied_total = 0
        if driver is None:
            log.warning(
                "worker.no_event_driver",
                worker=WORKER_NAME,
                detail="driver が無いのでイベントを取り込まない（周期処理だけが動く）",
            )
        if sink is None:
            log.warning(
                "worker.no_assertion_sink",
                worker=WORKER_NAME,
                detail="sink が無いので信念を書かない（計算はするが永続化しない）",
            )

    @property
    def coupling_period(self) -> timedelta:
        """実体間結合の実行周期。"""
        return timedelta(seconds=self.settings.reasoning.bp_period_s)

    def coupling_due(self, *, at: datetime) -> bool:
        """この時点で結合を回すべきか。

        Args:
            at: 判定時刻。
        """
        if self._last_coupling is None:
            return True
        return at - self._last_coupling >= self.coupling_period

    def mark_coupling_done(self, *, at: datetime) -> None:
        """結合を回したことを記録する。

        Args:
            at: 実行時刻。
        """
        self._last_coupling = at

    def attach_driver(self, driver: EventDriver) -> None:
        """driver を後から差す。

        ``KafkaEventConsumer`` はハンドラを構築時に受け取るので、ワーカー → consumer → driver の
        順でしか組めない（``build_worker`` を参照）。構築時に渡せないのはこの循環のためである。

        Args:
            driver: イベントの取得。
        """
        self._driver = driver

    async def handle_event(self, event: EventEnvelope) -> None:
        """イベント 1 件を信念に反映する（driver に渡すハンドラ）。

        **ここが副作用の本体である。** ``sbp.eventlog.process_once`` は冪等記録を副作用の前に
        付けるので、この関数が戻った時点で「処理済み」が確定する。バッファに溜めて後で書く形に
        すると、溜めた後に落ちたイベントが永久に失われる（処理済みなのに信念に入っていない）。

        例外を握りつぶさない。失敗を上に投げると ``process_once`` が冪等記録を取り消し、
        再配送で再試行できる状態に戻る——リトライは consumer 層の仕事である（CLAUDE ルール）。

        Args:
            event: 処理するイベント。
        """
        outcome = self.ingest.handle(event)
        if not outcome.applied:
            return
        assert outcome.assertion is not None  # applied が真なら必ずある
        self._write(outcome.assertion)
        self.applied_total += 1

    async def step(self) -> int:
        """1 ループ。最大 ``batch_size`` 件を処理し、必要なら結合を回す。

        **イベントが取れなくても結合の周期判定は進める。** 取れた件数で早期 return すると、
        イベントが止まっている間は結合も止まり、「静かに何もしていない」状態になる。

        Returns:
            このループで信念を更新した件数（取り込んだイベント数ではない。入れなかった
            イベントは ``ingest.skipped`` に理由別で積まれる）。
        """
        before = self.applied_total
        received = await self._drive()

        at = now()
        if self.coupling_due(at=at):
            self.mark_coupling_done(at=at)
            log.info(
                "worker.coupling_cycle",
                worker=WORKER_NAME,
                period_s=self.coupling_period.total_seconds(),
                tracked=self.ingest.tracked_entities,
            )
        applied = self.applied_total - before
        if received:
            log.info(
                "worker.belief_batch",
                worker=WORKER_NAME,
                received=received,
                applied=applied,
                skipped=dict(self.ingest.skipped),
            )
        return applied

    async def _drive(self) -> int:
        """driver に最大 ``batch_size`` 件を処理させる。

        ``driver`` が無ければ 0 を返す。**空の成功として振る舞うのはここだけ**で、
        その事実は起動時に警告として出している（設定漏れを静かに通さない）。
        """
        if self._driver is None:
            return 0
        return await self._driver(self.handle_event, self.settings.reasoning.batch_size)

    def _write(self, assertion: Assertion) -> None:
        """アサーションを書く。``sink`` が無ければ書かない（dry 相当）。"""
        if self._sink is None:
            return
        self._sink(assertion)

    async def run(
        self, *, max_iterations: int | None = None, commit: CommitHook | None = None
    ) -> int:
        """起動する。

        Args:
            max_iterations: テスト用の上限。
            commit: 終了時のオフセットコミット（``KafkaEventConsumer.stop`` を渡す）。
                **飛ばすと at-least-once の再処理が毎回の再起動で起きる。**
        """
        loop = asyncio.get_running_loop()
        self.runtime.install_signal_handlers(loop)
        return await self.runtime.run(self.step, max_iterations=max_iterations, commit=commit)


def kafka_driver(consumer: KafkaEventConsumer) -> EventDriver:
    """``KafkaEventConsumer`` を driver の形に合わせる。

    **ハンドラは consumer の構築時に固定される**ので、ここでは渡されたハンドラを使わず、
    consumer が自分のハンドラで処理する。呼び出し側は同じハンドラで consumer を作ること
    ——``build_worker`` がその順序を担保する。

    Args:
        consumer: 起動済みの consumer。

    Returns:
        ``(ハンドラ, 最大件数) -> 処理件数``。
    """

    async def drive(handler: EventHandler, limit: int) -> int:
        del handler  # consumer 構築時に固定済み（build_worker が同一性を担保する）
        return await consumer.run(max_events=limit)

    return drive


async def build_worker(
    settings: Settings | None = None, *, site_id: str
) -> tuple[BeliefWorker, KafkaEventConsumer]:
    """実運用の配線を組む（Redpanda → 信念更新 → Oxigraph）。

    **ハンドラの同一性に注意する。** ``KafkaEventConsumer`` はハンドラを構築時に受け取るので、
    ワーカーを先に作り、そのワーカーの ``handle_event`` を consumer に渡す。順序を逆にすると、
    consumer が別のハンドラを握ったまま動く。

    冪等ストアは Postgres 版（``processed_events`` テーブル）を使う。メモリ版だと再起動のたびに
    全イベントを再処理するので、at-least-once の意味が無くなる。

    Args:
        settings: 設定（既定は環境から読む）。
        site_id: 拠点識別子（名前付きグラフとトピック名の構成要素）。

    Returns:
        ``(ワーカー, consumer)``。呼び出し側が ``consumer.stop`` を ``run(commit=...)`` に渡す。
    """
    import psycopg  # noqa: PLC0415 — 実運用の配線でのみ使う

    from sbp.core.events import EventLayer, topic_name  # noqa: PLC0415
    from sbp.eventlog.consumer import ConsumerPolicy, KafkaEventConsumer  # noqa: PLC0415
    from sbp.eventlog.idempotency import PostgresIdempotencyStore  # noqa: PLC0415
    from sbp.knowledge import AssertionStore, HttpGraphStore  # noqa: PLC0415

    resolved = settings or Settings()
    graph = AssertionStore(HttpGraphStore(resolved), site_id=site_id)

    connection = await psycopg.AsyncConnection.connect(resolved.pg_dsn, autocommit=True)
    worker = BeliefWorker(resolved, sink=graph.assert_)

    consumer = KafkaEventConsumer(
        resolved,
        topics=(topic_name(site_id, EventLayer.BRIDGE, "physical"),),
        policy=ConsumerPolicy(consumer_group=WORKER_NAME),
        store=PostgresIdempotencyStore(connection),
        handler=worker.handle_event,
    )
    await consumer.start()
    worker.attach_driver(kafka_driver(consumer))
    log.info("worker.wired", worker=WORKER_NAME, site_id=site_id)
    return worker, consumer


def main() -> int:
    """エントリポイント。

    拠点は ``Settings.site_id``（``SBP_SITE_ID``、既定 ``siteA``）。**読むトピックと書く
    グラフの両方がこの 1 つの値から決まる**ので、取り違えると拠点間の信念が混ざる。
    複数拠点を動かすときはプロセスを拠点ごとに分ける（1 ワーカー 1 プロセス）。
    """

    async def _run() -> None:
        settings = Settings()
        worker, consumer = await build_worker(settings, site_id=settings.site_id)
        await worker.run(commit=consumer.stop)

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
