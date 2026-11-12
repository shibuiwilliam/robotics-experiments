"""照合ワーカー（M7 の運用タスク）。

物理観測由来のイベントと業務記録由来のイベントを時間窓に溜め、周期ごとに五類型の検出規則を
回して不一致を起票する（設計書 §8.2）。

**イベントごとに判定しない。** 照合は点ではなく区間を見る——持続 τ を超えて乖離が続いて初めて
不一致とするので（``DetectionContext.persistence``、既定 300 秒）、1 件のイベントでは判定が
成立しない。窓の管理は ``sbp.reasoning.windows`` にあり、このモジュールは配線だけを持つ。

**検出の周期と信念更新の周期は別である。** 信念更新はイベントごと、照合は窓ごと。同じ周期に
すると、照合を速くするために信念更新を遅くするか、その逆をすることになる。

**起票は「イベントを出す」ところまで。** 不一致を知識グラフへ書かない（``bridge`` は永続化
しないのと同じ理由ではなく、**不一致はイベントであって信念ではない**から）。本体は作業項目表
（``discrepancies``、ADR-0013）に書く。人の解決を経て学習信号になる経路は UI（M7-04）と
``/commands/*`` を通る。

**深刻度の業務価値は運用の配線で渡す**（M8-30）。``value/*.yaml``（ObjectType ごとの v(x)）と
``reliability/*.yaml``（情報源の信頼性）を読み、実体の型は毎周期知識グラフから引く。構築子の
既定（空のレジストリ）でも動いてしまうので、渡されなかったものは ``unwired`` に数えて警告する
——渡し忘れると全件の価値項が中間値になり、深刻度の順位が平らになる（``docs/PROGRESS.md``
所見 109）。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from sbp.core import vocab
from sbp.core.belief import BooleanBelief
from sbp.core.config import Settings
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.reasoning.ingest import fluent_of_event
from sbp.reasoning.reconciliation import DetectionContext, reconcile
from sbp.reasoning.reconciliation.notify import discrepancy_event
from sbp.reasoning.reconciliation.persistence import to_record
from sbp.reasoning.reconciliation.rules import detect_state
from sbp.reasoning.windows import WindowStore
from services.metrics import MetricRecorder
from services.runtime import ReadinessCheck, WorkerRuntime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import datetime

    from sbp.core.events import EventEnvelope
    from sbp.eventlog.consumer import EventHandler, KafkaEventConsumer
    from sbp.eventlog.producer import KafkaEventProducer
    from sbp.integration.reliability import ReliabilityRegistry
    from sbp.knowledge.discrepancies import DiscrepancyRecord
    from sbp.reasoning.reconciliation.discrepancy import Discrepancy
    from sbp.reasoning.reconciliation.engine import ReconciliationOutcome
    from sbp.reasoning.reconciliation.severity import BusinessValueRegistry

    EventDriver = Callable[[EventHandler, int], Awaitable[int]]
    """``(ハンドラ, 最大件数) -> 処理した件数``（``belief_worker`` と同じ形）。"""
    DiscrepancyEventSink = Callable[[Discrepancy], object]
    """不一致 1 件を出す（イベント送信）。"""
    DiscrepancySink = Callable[[DiscrepancyRecord], object]
    """不一致 1 件を作業項目として書く（``DiscrepancyStore.upsert`` を包む）。"""
    CommitHook = Callable[[], Awaitable[None]]
    """終了時のオフセットコミット。"""
    EntityTypesProvider = Callable[[], Mapping[str, str]]
    """実体 IRI → ObjectType IRI を返す（業務価値の引き当て。毎周期呼ぶ）。"""

log = get_logger(__name__)

WORKER_NAME = "reconcile-worker"

VALUE_DIR = Path("value")
"""業務価値（v(x)）の定義の置き場（リポジトリの根から）。"""

RELIABILITY_DIR = Path("reliability")
"""情報源信頼性モデルの初期値の置き場（リポジトリの根から）。"""


def readiness_checks(settings: Settings) -> tuple[ReadinessCheck, ...]:
    """このワーカーの依存（イベントログと知識グラフ）。

    知識グラフは実体の型（業務価値の引き当て）を読むために要る（M8-30）。不一致の本体は
    Postgres の作業項目表に書くが、その接続は ``build_worker`` が構築時に張るので、ここでは
    検査しない（繋がらなければ構築で落ちる）。

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


class ReconcileWorker:
    """窓への蓄積と周期的な照合。

    Attributes:
        settings: 設定。
        runtime: 実行ループ。
        windows: 時間窓。
        unwired: 渡されなかった依存の名前（``values`` / ``reliability`` / ``entity_types``）。
            空のレジストリで動いてしまうので落とさず、ここに数えて警告する（M8-30）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        driver: EventDriver | None = None,
        sink: DiscrepancyEventSink | None = None,
        values: BusinessValueRegistry | None = None,
        reliability: ReliabilityRegistry | None = None,
        entity_types: dict[str, str] | None = None,
        entity_types_provider: EntityTypesProvider | None = None,
        metrics: MetricRecorder | None = None,
        store: DiscrepancySink | None = None,
    ) -> None:
        """Args:
        settings: 設定（既定は環境から読む）。
        driver: イベントの取得（``None`` なら取り込まない。起動時に警告する）。
        sink: 不一致の出力（``None`` なら出さない。検出だけ行う）。
        values: 業務価値のレジストリ（深刻度の v(x)）。
        reliability: 情報源信頼性のレジストリ（抑制判定に使う）。
        entity_types: 実体 IRI → ObjectType IRI（業務価値の引き当て）。固定の対応で、
            ``entity_types_provider`` の結果より優先する（検証やカナリアで差し替えるため）。
        entity_types_provider: 実体の型を毎周期引く（運用では知識グラフ。M8-30）。
            資産台帳の同期で実体が増えたときに古い対応のまま評価しないよう、キャッシュしない。
        metrics: 指標の記録先（M8-14）。
        store: 不一致の作業項目の書き込み先（ADR-0013）。
        """
        from sbp.integration.reliability import ReliabilityRegistry  # noqa: PLC0415
        from sbp.reasoning.reconciliation.severity import (  # noqa: PLC0415
            BusinessValueRegistry,
        )

        self.settings = settings or Settings()
        self.runtime = WorkerRuntime(
            name=WORKER_NAME,
            settings=self.settings,
            checks=readiness_checks(self.settings),
        )
        self.windows = WindowStore(
            min_span=timedelta(seconds=self.settings.reasoning.persistence_s),
            step=timedelta(seconds=self.settings.reasoning.filter_step_s),
            capacity=self.settings.reasoning.window_samples,
        )
        self._driver = driver
        self._sink = sink
        self._metrics = metrics
        self._store = store
        # 空のレジストリを既定にする。**価値が登録されていない型は深刻度の価値項が
        # 中間値になる**（0 ではない——`BusinessValueRegistry` の設計）。登録忘れを
        # 「価値ゼロ＝起票しない」にしないための仕様である。
        self._values = (
            values
            if values is not None
            else BusinessValueRegistry(
                {}, default_scale=self.settings.reasoning.severity_value_scale
            )
        )
        self._reliability = reliability if reliability is not None else ReliabilityRegistry({})
        self._entity_types = entity_types or {}
        self._entity_types_provider = entity_types_provider
        self.unwired = tuple(
            name
            for name, given in (
                ("values", values),
                ("reliability", reliability),
                ("entity_types", entity_types or entity_types_provider),
            )
            if given is None
        )
        if self.unwired:
            log.warning(
                "worker.registries_not_wired",
                worker=WORKER_NAME,
                missing=list(self.unwired),
                detail="空の既定で動く。深刻度の業務価値項が全件中間値になる（所見 109）",
            )
        self._last_detection: datetime | None = None
        self.raised_total = 0
        self.suppressed_total = 0
        if driver is None:
            log.warning(
                "worker.no_event_driver",
                worker=WORKER_NAME,
                detail="driver が無いのでイベントを取り込まない（周期処理だけが動く）",
            )
        if sink is None:
            log.warning(
                "worker.no_discrepancy_sink",
                worker=WORKER_NAME,
                detail="sink が無いので不一致を出さない（検出はするが誰にも届かない）",
            )

    def attach_driver(self, driver: EventDriver) -> None:
        """driver を後から差す（consumer がハンドラを構築時に要求するため）。

        Args:
            driver: イベントの取得。
        """
        self._driver = driver

    @property
    def detection_period(self) -> timedelta:
        """照合を回す周期。

        **信念更新の周期（``bp_period_s``）と分けている。** 同じ値にすると、照合を速くするために
        信念更新を遅くするか、その逆をすることになる。
        """
        return timedelta(seconds=self.settings.reasoning.reconcile_period_s)

    def detection_due(self, *, at: datetime) -> bool:
        """この時点で照合を回すべきか。

        Args:
            at: 判定時刻。
        """
        if self._last_detection is None:
            return True
        return at - self._last_detection >= self.detection_period

    def mark_detection_done(self, *, at: datetime) -> None:
        """照合を回したことを記録する（``belief_worker.mark_coupling_done`` と同じ形）。

        Args:
            at: 実行時刻。
        """
        self._last_detection = at

    async def handle_event(self, event: EventEnvelope) -> None:
        """イベント 1 件を窓に入れる（driver に渡すハンドラ）。

        **様相で観測側と記録側に振り分ける。** ``observed`` は業務記録の存在そのもの（統合層が
        出す）、``inferred`` は物理観測からの推定（接地層・信念更新が出す）。この区別が
        照合の両列になる——取り違えると、記録と観測を突き合わせているつもりで同じ側を 2 回
        見ることになる。

        窓への追加だけなので失敗しない。例外を投げる経路が無いが、**冪等記録は副作用の前に
        付く**ので、将来ここに失敗しうる処理を足すときは例外を上に投げること
        （``belief_worker.handle_event`` の docstring 参照）。

        Args:
            event: 処理するイベント。
        """
        fluent_local = fluent_of_event(str(event.type))
        if fluent_local is None:
            return
        fluent_iri = _fluent_iri(fluent_local)
        if fluent_iri is None:
            return
        if not isinstance(event.belief, BooleanBelief):
            return

        entity = str(event.subject)
        if event.modality is Modality.OBSERVED:
            # 記録由来。観測の刻みが無ければ入らない（窓の設計）
            self.windows.record(
                entity=entity, fluent=fluent_iri, at=event.tx_time, probability=event.belief.p
            )
        elif event.modality is Modality.INFERRED:
            self.windows.observe(
                entity=entity, fluent=fluent_iri, at=event.tx_time, probability=event.belief.p
            )

    async def step(self) -> int:
        """1 ループ。イベントを取り込み、周期が来たら照合を回す。

        **イベントが取れなくても周期判定は進める**（``belief_worker`` と同じ理由）。

        Returns:
            このループで起票した不一致の件数。
        """
        received = await self._drive()

        at = now()
        raised = 0
        if self.detection_due(at=at):
            self.mark_detection_done(at=at)
            raised = self._detect(at=at)
        if self._metrics is not None:
            await self._metrics.flush(at=at)
        if received or raised:
            log.info(
                "worker.reconcile_cycle",
                worker=WORKER_NAME,
                received=received,
                windows=len(self.windows),
                raised=raised,
            )
        return raised

    def _detect(self, *, at: datetime) -> int:
        """窓から検出して起票する。

        現状は**類型 2（状態不一致）だけ**を回す。類型 1・3 は記録側の構造
        （``RecordedPresence`` / ``TimedClaim``）を必要とし、類型 4・5 は Track と規範の
        取り込みを必要とするので、イベント 2 本から組める類型に限っている。
        ここを広げるときは、窓に何を溜めるかから設計し直すことになる。

        **抑制の判定に記録（``records``）を渡していない。** 類型 2 を抑制できる系統誤差は
        ``template_copy`` だけで、その判定（``SystematicBias.explains``）は時刻のずれでは
        説明できないので常に偽を返す——記録を渡しても類型 2 は抑制されない（M8-30 で確認）。
        窓は記録の確率しか持たず ``Record`` を持たないので、渡すには窓の設計から変える必要が
        ある。類型 1・3 を回すときに一緒に扱う。

        Args:
            at: 検出時刻。

        Returns:
            起票した件数。
        """
        series = self.windows.ready_series()
        if not series:
            return 0
        # 記録列を持たない列は類型 2 を判定できない（片側だけ見ても乖離が定義できない）
        comparable = [single for single in series if single.has_records]
        if not comparable:
            log.debug(
                "worker.no_comparable_series",
                worker=WORKER_NAME,
                windows=len(self.windows),
                detail="記録列を持つ窓が無い。統合層のイベントが届いていない可能性",
            )
            return 0

        context = DetectionContext.from_settings(
            self.settings.reasoning,
            now=at,
            ontology_version=vocab.ONTOLOGY_VERSION,
            values=self._values,
            reliability=self._reliability,
            entity_types=self._current_entity_types(),
        )
        candidates = detect_state(comparable, context=context)
        outcome = reconcile(candidates, reliability=self._reliability, at=at)

        # **永続化してから通知する**（ADR-0013）。順序を逆にすると、通知を見た購読側が
        # 表を引いて「無い」と答えることになる——イベントは通知であって本体ではないので、
        # 本体が先に存在していなければならない
        self._persist(outcome.triaged + outcome.suppressed)
        for discrepancy in outcome.triaged:
            self._emit(discrepancy)
        self.raised_total += len(outcome.triaged)
        self.suppressed_total += len(outcome.suppressed)
        self._measure(outcome, at=at)
        if outcome.suppressed:
            # 抑制したことを数える。**抑制率を見ないと「不一致が無い」と区別できない**
            log.info(
                "worker.suppressed",
                worker=WORKER_NAME,
                count=len(outcome.suppressed),
                reasons=[d.suppression.reason for d in outcome.suppressed if d.suppression],
            )
        return len(outcome.triaged)

    def _current_entity_types(self) -> dict[str, str]:
        """この周期の「実体 → ObjectType」。provider の結果に固定の対応を上書きする。

        照合する列がある周期だけ呼ぶ（記録が届かない間に毎周期グラフを読まない）。
        """
        found: dict[str, str] = (
            {} if self._entity_types_provider is None else dict(self._entity_types_provider())
        )
        found.update(self._entity_types)
        return found

    def _persist(self, discrepancies: Sequence[Discrepancy]) -> None:
        """不一致を作業項目として書く（ADR-0013）。

        **抑制したものも書く。** 抑制は「系統誤差で説明できる」という判断であり、その妥当性を
        人が棚卸しする（OPS-03）。書かないと抑制が見えず、「不一致が無い」と区別できない。

        ``store`` が無ければ書かない（単体テストと、表を持たない実行）。

        Args:
            discrepancies: 書く不一致。
        """
        if self._store is None or not discrepancies:
            return
        for discrepancy in discrepancies:
            self._store(to_record(discrepancy))

    def _measure(self, outcome: ReconciliationOutcome, *, at: datetime) -> None:
        """照合 1 周ぶんの指標を記録する（M8-14）。

        **起票 0 件でも記録する。** 「起票が無い」ことと「照合が動いていない」ことは、
        指標の行が来るかどうかでしか区別できない（所見 32）。抑制率も同じ理由で、
        抑制が 0 のときに 0.0 を書く。

        Args:
            outcome: 照合の結果。
            at: 照合時刻。
        """
        if self._metrics is None:
            return
        kinds = {str(int(d.kind)) for d in outcome.triaged}
        # 類型ごとに起票数を出す（運用で見るのは「どの類型が増えたか」である）
        for kind in sorted(kinds):
            count = sum(1 for d in outcome.triaged if str(int(d.kind)) == kind)
            self._metrics.record("discrepancies_raised", float(count), at=at, labels={"kind": kind})
        if not kinds:
            self._metrics.record("discrepancies_raised", 0.0, at=at, labels={"kind": "none"})

        detected = outcome.total_detected
        rate = 0.0 if detected == 0 else len(outcome.suppressed) / detected
        self._metrics.record("suppression_rate", rate, at=at)

    def _emit(self, discrepancy: Discrepancy) -> None:
        """不一致を出す。``sink`` が無ければ出さない。"""
        if self._sink is None:
            return
        self._sink(discrepancy)

    async def _drive(self) -> int:
        """driver に最大 ``batch_size`` 件を処理させる。"""
        if self._driver is None:
            return 0
        return await self._driver(self.handle_event, self.settings.reasoning.batch_size)

    async def run(
        self, *, max_iterations: int | None = None, commit: CommitHook | None = None
    ) -> int:
        """起動する。

        Args:
            max_iterations: テスト用の上限。
            commit: 終了時のオフセットコミット。
        """
        loop = asyncio.get_running_loop()
        self.runtime.install_signal_handlers(loop)
        return await self.runtime.run(self.step, max_iterations=max_iterations, commit=commit)


def _fluent_iri(local_name: str) -> str | None:
    """フルーエントのローカル名を IRI にする（不変条件 5: vocab からのみ）。

    Args:
        local_name: ``Running`` などのローカル名。

    Returns:
        IRI、未知のローカル名なら ``None``。
    """
    value = getattr(vocab.ent, local_name, None)
    return None if value is None else str(value)


def discrepancy_topic(settings: Settings, *, site_id: str) -> str:
    """不一致の起票先トピック（ADR-0010）。

    既定は拠点（``<site>.reasoning.discrepancy``）。``SBP_REASONING_DISCREPANCY_SCOPE=central``
    で中央に切り替わる。**両方には出さない**——同じ不一致が 2 つの ``event_id`` を持ち、
    冪等性（不変条件 4）が壊れる。

    Args:
        settings: 設定。
        site_id: 検出した拠点。

    Returns:
        トピック名。
    """
    from sbp.core.events import EventLayer, topic_name  # noqa: PLC0415

    scope = site_id if settings.reasoning.discrepancy_scope == "site" else "central"
    return topic_name(scope, EventLayer.REASONING, "discrepancy")


def load_registries(
    settings: Settings, *, root: Path = Path()
) -> tuple[BusinessValueRegistry, ReliabilityRegistry]:
    """業務価値と情報源信頼性のレジストリを読む（M8-30）。

    **正規化の基準額は設定から渡す**（``SBP_REASONING_SEVERITY_VALUE_SCALE``）。レジストリ側に
    既定を持たせると二重定義になる。どちらも定義が 1 件も無ければ例外になる——空のレジストリで
    動き続けると、深刻度の価値項が全件中間値になったことに誰も気付かない（所見 109）。

    Args:
        settings: 設定。
        root: リポジトリの根（``value/`` と ``reliability/`` の親）。

    Returns:
        ``(業務価値, 信頼性)``。
    """
    from sbp.integration.reliability import ReliabilityRegistry  # noqa: PLC0415
    from sbp.reasoning.reconciliation.severity import BusinessValueRegistry  # noqa: PLC0415

    values = BusinessValueRegistry.from_directory(
        root / VALUE_DIR, default_scale=settings.reasoning.severity_value_scale
    )
    reliability = ReliabilityRegistry.from_directory(root / RELIABILITY_DIR)
    return values, reliability


async def build_worker(
    settings: Settings | None = None, *, site_id: str
) -> tuple[ReconcileWorker, KafkaEventConsumer, KafkaEventProducer]:
    """実運用の配線を組む（物理 + 業務イベント → 不一致イベント）。

    **2 つのトピックを購読する。** 物理（接地層）と業務（統合層）の両方が要る——片方だけだと
    照合の片側しか埋まらず、``has_records`` が偽のまま 1 件も検出されない。

    **業務価値・信頼性・実体の型を渡す**（M8-30）。実体の型は知識グラフから、業務価値が
    登録されている型に絞って毎周期引く（``most_specific_types``）。渡さないと構築子の既定
    （空のレジストリ）で動き、深刻度の価値項が全件中間値になる（所見 109）。

    Args:
        settings: 設定（既定は環境から読む）。
        site_id: 拠点識別子。

    Returns:
        ``(ワーカー, consumer, producer)``。呼び出し側が consumer と producer の停止を担う。
    """
    import psycopg  # noqa: PLC0415 — 実運用の配線でのみ使う

    from sbp.core.events import EventLayer, topic_name  # noqa: PLC0415
    from sbp.eventlog.consumer import ConsumerPolicy, KafkaEventConsumer  # noqa: PLC0415
    from sbp.eventlog.idempotency import PostgresIdempotencyStore  # noqa: PLC0415
    from sbp.eventlog.producer import KafkaEventProducer  # noqa: PLC0415
    from sbp.knowledge import (  # noqa: PLC0415
        DiscrepancyStore,
        HttpGraphStore,
        MetricStore,
        most_specific_types,
    )
    from services.belief_worker import kafka_driver  # noqa: PLC0415

    resolved = settings or Settings()
    values, reliability = load_registries(resolved)
    graph = HttpGraphStore(resolved)

    def entity_types() -> Mapping[str, str]:
        """業務価値が登録されている型に絞って、実体の最も具体的な型を引く。"""
        return most_specific_types(graph, types=values.object_types, site=site_id)

    connection = await psycopg.AsyncConnection.connect(resolved.pg_dsn, autocommit=True)

    producer = KafkaEventProducer(resolved)
    await producer.start()
    topic = discrepancy_topic(resolved, site_id=site_id)

    def sink(discrepancy: Discrepancy) -> object:
        """起票を通知する（ADR-0010）。

        **イベントは通知であって不一致そのものではない。** 本体は ``persist`` が作業項目表に
        書く（M8-12、ADR-0013）。
        """
        return asyncio.create_task(
            producer.send(topic, discrepancy_event(discrepancy, site_id=site_id))
        )

    store = DiscrepancyStore(connection, site=site_id)

    def persist(record: DiscrepancyRecord) -> object:
        """作業項目として書く（ADR-0013。本体は表、通知はイベント）。"""
        return asyncio.create_task(store.upsert(record))

    worker = ReconcileWorker(
        resolved,
        sink=sink,
        values=values,
        reliability=reliability,
        entity_types_provider=entity_types,
        metrics=MetricRecorder(site=site_id, sink=MetricStore(connection, site=site_id).write),
        store=persist,
    )

    consumer = KafkaEventConsumer(
        resolved,
        topics=(
            topic_name(site_id, EventLayer.BRIDGE, "physical"),
            topic_name(site_id, EventLayer.INTEGRATION, "business"),
        ),
        policy=ConsumerPolicy(consumer_group=WORKER_NAME),
        store=PostgresIdempotencyStore(connection),
        handler=worker.handle_event,
    )
    await consumer.start()
    worker.attach_driver(kafka_driver(consumer))
    log.info("worker.wired", worker=WORKER_NAME, site_id=site_id, discrepancy_topic=topic)
    return worker, consumer, producer


def main() -> int:
    """エントリポイント。

    不一致の起票先は ``SBP_REASONING_DISCREPANCY_SCOPE``（既定 ``site``）で決まる
    （ADR-0010）。拠点は ``Settings.site_id``。
    """

    async def _run() -> None:
        settings = Settings()
        worker, consumer, producer = await build_worker(settings, site_id=settings.site_id)
        try:
            await worker.run(commit=consumer.stop)
        finally:
            await producer.stop()

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
