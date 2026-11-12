"""信念更新ワーカー（M5-08）。

プローブ由来のイベント（``<site>.bridge.physical``）を読み、実体単位の前向きフィルタを進めて
``inferred`` アサーションを書く。実体間結合（loopy BP）は ``bp_period_s`` ごとに回す。

**三源を読む**（M8-26、``docs/PROGRESS.md`` 所見 102）:

- 物理観測: ``<site>.bridge.physical``（接地のプローブ出力）→ 観測尤度。
- 業務記録: ``<site>.integration.business``。取込（ingest-worker）が信頼性モデルを通した確率の
  「主張イベント」（``BusinessEvent/<fluent>``）にして出す（ADR-0016）→ 記録尤度。存在イベント
  （型が記録型）はフルーエントではないので取り込まない。
- 計画: 知識グラフの ``planned`` の文（区間と遵守率）を事前に混ぜる（:class:`GraphPlans`）。

記録の監査隔離（不変条件 6）は :meth:`sbp.reasoning.ingest.BeliefIngest.handle` が主張イベントの
証拠（``RecordEvidence``）にマスクを適用する。

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
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.config import Settings
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.core.time import Interval, now
from sbp.knowledge.contributions import ContributionRecord
from sbp.knowledge.relations import (
    aggregation_rules,
    group_by_whole,
    part_whole_edges,
    situation_members,
)
from sbp.reasoning.belief import PlannedSpan
from sbp.reasoning.coupling import AggregationRule, PartWhole, SituationLink, propagate
from sbp.reasoning.ingest import BeliefIngest, default_transition
from services.metrics import MetricRecorder
from services.runtime import ReadinessCheck, WorkerRuntime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import datetime

    from sbp.core.events import EventEnvelope
    from sbp.eventlog.consumer import EventHandler, KafkaEventConsumer
    from sbp.knowledge.assertions import AssertionStore
    from sbp.knowledge.store import GraphStore
    from sbp.reasoning.ingest import IngestOutcome

    EventDriver = Callable[["EventHandler", int], Awaitable[int]]
    """``(ハンドラ, 最大件数) -> 処理した件数``。

    実運用では ``KafkaEventConsumer.run(max_events=...)`` を包む。単体テストでは一覧から
    ハンドラを呼ぶ。**ハンドラを渡す形にしてあるのが要点である**——冪等記録は副作用の前に
    付くので、ハンドラが本体の仕事をしないと at-least-once が壊れる。
    """
    AssertionSink = Callable[[Assertion], object]
    """アサーションを 1 件書く。戻り値は使わない（``assert_`` は ID を返す）。"""
    ContributionSink = Callable[["ContributionRecord"], Awaitable[None]]
    """信念 1 件の三源の寄与を書く（``ContributionStore.write``。ADR-0020）。"""
    CommitHook = Callable[[], Awaitable[None]]
    """終了時のオフセットコミット。"""
    RelationProvider = Callable[
        [Mapping[tuple[str, str], float]], tuple[Sequence[PartWhole], Sequence[SituationLink]]
    ]
    """``事前 -> (部分−全体の結合, 同一 Situation の結合)``。

    事前を渡すのは、関係の参照する実体を事前の集合で絞るためである（``propagate`` は結合が
    参照する実体すべての事前を要求する）。**2 種類を返す**のは、両者の因子の形が違うため
    ——部分−全体は向きと集約規則を持ち、同一 Situation は対称で規則を持たない。
    """

log = get_logger(__name__)

WORKER_NAME = "belief-worker"

_COUPLING_MIN_CHANGE = 0.01
"""結合でこの幅より小さく動いた信念は書かない。

**追記専用なので、変化のない文を毎周期足すとグラフが単調に膨らむ**（60 秒周期なら 1 日で
1440 件/実体）。二時制クエリの履歴が結合の周期で埋まり、「いつ信念が変わったか」が読めなくなる。
閾値を置く代わりに、動いた分だけを記録する。"""


class GraphPlans:
    """知識グラフの ``planned`` の文から、時刻に有効な計画を引く（M8-26）。

    **周期ごとに読み直す。** 計画は取込（ingest-worker）が置き換えるので、キャッシュしっぱなしに
    すると変更された計画で事前を作り続ける。1 イベントごとにクエリすると Oxigraph への往復が
    イベント数に比例するので、``step()`` の頭で :meth:`refresh` してその周期の間だけ覚える。
    """

    def __init__(self, store: AssertionStore) -> None:
        """Args:
        store: 二時制アサーション API（拠点のグラフ）。
        """
        self._store = store
        self._cache: dict[tuple[str, str], list[PlannedSpan]] = {}

    def refresh(self) -> None:
        """覚えた計画を捨てる（次の引き当てで読み直す）。"""
        self._cache.clear()

    def __call__(self, entity: str, fluent: str, at: datetime) -> PlannedSpan | None:
        """``at`` に有効な計画（無ければ ``None``）。

        Args:
            entity: 実体 IRI。
            fluent: フルーエント型の IRI（``planned`` の文の述語）。
            at: 引き当てる時刻。
        """
        key = (entity, fluent)
        spans = self._cache.get(key)
        if spans is None:
            spans = [
                PlannedSpan(
                    planned_interval=Interval(row.valid_from, row.valid_to),
                    adherence_prior=row.confidence,
                )
                for row in self._store.query(
                    subject=entity, predicate=fluent, modality=Modality.PLANNED
                )
            ]
            self._cache[key] = spans
        for span in spans:
            if span.planned_interval.contains_instant(at):
                return span
        return None


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
        ReadinessCheck(name="redpanda", probe=lambda: tcp_reachable(settings.kafka_bootstrap)),
        ReadinessCheck(name="oxigraph", probe=lambda: http_reachable(settings.sparql_query_url)),
    )


def tcp_reachable(bootstrap: str) -> bool:
    """``host:port`` に TCP で到達できるか。"""
    import socket  # noqa: PLC0415 — 疎通検査だけに使う

    host, _, port = bootstrap.partition(":")
    try:
        with socket.create_connection((host or "localhost", int(port or 9092)), timeout=2.0):
            return True
    except OSError:
        return False


def http_reachable(url: str) -> bool:
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
        relations: RelationProvider | None = None,
        metrics: MetricRecorder | None = None,
        plans: GraphPlans | None = None,
        contributions: ContributionSink | None = None,
    ) -> None:
        """Args:
        settings: 設定（既定は環境から読む）。
        driver: ``(ハンドラ, 最大件数) -> 処理件数``（``None`` なら取り込まない。
            設定漏れは起動時に警告する）。
        sink: アサーションの書き込み（``None`` なら書かない）。
        ingest: 信念更新の配線（既定は ``vocab`` の述語表で作る）。
        relations: 事前 → 強い関係（``None`` なら結合を回さない）。**事前を引数に取るのは、
            ``propagate`` が結合の参照する実体すべての事前を要求するため**——持っていない
            実体を含む関係を渡すと例外になる。
        metrics: 指標の記録（``None`` なら測らない）。M8-14。
        plans: 計画の引き当て（``None`` なら計画源は使わない。``contrib_plan`` は 0 になる）。
        contributions: 三源の寄与の書き込み（``None`` なら残さない。証拠の並置が「寄与は記録
            されていない」になる。ADR-0020）。
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
        self._relations = relations
        self._metrics = metrics
        self.ingest = ingest if ingest is not None else default_ingest(self.settings)
        self._plans = plans
        self._contributions = contributions
        if plans is not None and self.ingest.plan_provider is None:
            self.ingest.plan_provider = plans
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
        started = now()
        outcome = self.ingest.handle(event)
        if not outcome.applied:
            return
        assert outcome.assertion is not None  # applied が真なら必ずある
        assert outcome.update is not None
        try:
            await self._persist(event, outcome)
        except Exception:
            # 書けなかった更新を、フィルタの状態に残さない。再配送で同じ証拠が二重に数えられ、
            # 主張の時刻が 1µs ずつ進む（M8-17 のレビュー）
            if outcome.undo is not None:
                outcome.undo()
            raise
        self.applied_total += 1
        self._measure(event, outcome, started=started)

    async def _persist(self, event: EventEnvelope, outcome: IngestOutcome) -> None:
        """寄与とアサーションを書く（寄与 → アサーションの順。ADR-0020）。

        Args:
            event: 処理したイベント。
            outcome: 信念更新の結果（更新した場合）。
        """
        assert outcome.assertion is not None
        assert outcome.update is not None
        if self._contributions is not None:
            # **寄与 → アサーションの順。** 逆だと、アサーションがあるのに源が分からない文が残る。
            # 寄与だけ残る孤児は無害（読み出しがアサーションと突き合わせる）。失敗は上に投げて
            # 再配送で再試行する（ADR-0020）
            contribution = outcome.update.contribution
            await self._contributions(
                ContributionRecord(
                    assertion_id=outcome.assertion.assertion_id,
                    entity=outcome.entity,
                    fluent=outcome.assertion.predicate,
                    at=outcome.update.at,
                    prior=outcome.update.prior,
                    posterior=outcome.update.posterior,
                    observed=contribution.observed,
                    record=contribution.record,
                    plan=contribution.plan,
                    source_event_id=str(event.event_id),
                    excluded_records=outcome.update.excluded_records,
                )
            )
        self._write(outcome.assertion)

    def _measure(self, event: EventEnvelope, outcome: IngestOutcome, *, started: datetime) -> None:
        """信念更新 1 件ぶんの指標を記録する（M8-14）。

        **寄与率は正規化する。** ``SourceContribution`` は対数尤度比の絶対値（大きさ）を
        持ち、``contrib_*`` 指標は単位が ``ratio``（0〜1）である。生の大きさをそのまま
        入れると ``MetricSample`` の値域検証で落ちる——落ちることで取り違えに気付ける。

        Args:
            event: 処理したイベント。
            outcome: 信念更新の結果。
            started: 処理の開始時刻（レイテンシの基準）。
        """
        if self._metrics is None:
            return
        at = now()
        update = outcome.update
        assert update is not None  # applied が真のときだけ呼ばれる
        labels = {"entity": outcome.entity, "fluent": outcome.fluent or ""}

        self._metrics.record("update_latency", (at - started).total_seconds(), at=at, labels=labels)
        # イベントログの滞留: 事象が起きた時刻から信念に入るまで。**負にならないよう
        # 下限 0 にする**——合成データや時計のずれで未来の事象が来ることがある
        lag = max(0.0, (at - event.valid_time.start).total_seconds())
        topic = "integration.business" if outcome.is_claim else "bridge.physical"
        self._metrics.record("event_log_lag", lag, at=at, labels={"topic": topic})

        contrib = update.contribution
        total = contrib.observed + contrib.record + contrib.plan
        if total > 0.0:
            for source, magnitude in (
                ("contrib_observed", contrib.observed),
                ("contrib_record", contrib.record),
                ("contrib_plan", contrib.plan),
            ):
                self._metrics.record(source, magnitude / total, at=at, labels=labels)

        self._metrics.record(
            "belief_entropy",
            BooleanBelief(p=update.posterior).entropy_nats,
            at=at,
            labels=labels,
        )

    async def step(self) -> int:
        """1 ループ。最大 ``batch_size`` 件を処理し、必要なら結合を回す。

        **イベントが取れなくても結合の周期判定は進める。** 取れた件数で早期 return すると、
        イベントが止まっている間は結合も止まり、「静かに何もしていない」状態になる。

        Returns:
            このループで信念を更新した件数（取り込んだイベント数ではない。入れなかった
            イベントは ``ingest.skipped`` に理由別で積まれる）。
        """
        before = self.applied_total
        if self._plans is not None:
            self._plans.refresh()
        received = await self._drive()

        at = now()
        if self.coupling_due(at=at):
            self.mark_coupling_done(at=at)
            coupled = self._couple(at=at)
            log.info(
                "worker.coupling_cycle",
                worker=WORKER_NAME,
                period_s=self.coupling_period.total_seconds(),
                tracked=self.ingest.tracked_entities,
                coupled=coupled,
            )
        if self._metrics is not None:
            await self._metrics.flush(at=at)

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

    def _couple(self, *, at: datetime) -> int:
        """実体間結合を 1 周回し、更新された信念を書く（設計書 §8.1）。

        **フィルタの状態を上書きしない。** 結合の事後をフィルタに書き戻すと、同じ関係からの
        メッセージが周期ごとに複利で積み上がる（2 周目は 1 周目の結合済み値を事前として
        また結合する）。フィルタは「自分の証拠だけによる推定」のまま保ち、結合は周期ごとの
        調整として別のアサーションで記録する。

        **収束しなかった結果も書く。** ``converged`` が偽でも、それは「まだ振動している」という
        情報であって無意味な値ではない——ただしログに出し、頻発するなら減衰係数と反復上限を
        見直す材料にする。黙って捨てると、結合が機能していないことに気付けない。

        Args:
            at: この周期の時刻。

        Returns:
            結合によって信念を書いた件数。
        """
        if self._relations is None:
            return 0
        # **結合の鍵はフルーエント型 IRI にする**（不変条件 5 の canonical な識別子）。
        # フィルタはローカル名で持っているが、集約規則（`aggregatesAs`）は IRI で引かれるので、
        # ここでローカル名と IRI が混ざると結合が静かに 1 件も成立しない（実際そうなった）。
        priors: dict[tuple[str, str], float] = {}
        for (entity, local_name), probe_filter in self.ingest.filters.items():
            if probe_filter.posterior is None:
                continue
            fluent_iri = self.ingest.predicate_of.get(local_name)
            if fluent_iri is None:
                continue
            priors[entity, fluent_iri] = probe_filter.posterior
        if not priors:
            return 0

        couplings, links = self._relations(priors)
        if not couplings and not links:
            return 0

        result = propagate(
            priors,
            part_wholes=couplings,
            situations=links,
            max_iterations=self.settings.reasoning.bp_max_iterations,
        )
        if not result.converged:
            log.info(
                "worker.coupling_not_converged",
                worker=WORKER_NAME,
                iterations=result.iterations,
                max_delta=result.max_delta,
                detail="振動している。頻発するなら damping と反復上限を見直す",
            )

        written = 0
        for (entity, fluent_iri), posterior in result.posteriors.items():
            if abs(posterior - priors[entity, fluent_iri]) < _COUPLING_MIN_CHANGE:
                # 動かなかった組は書かない。**追記専用なので、変化のない文を毎周期足すと
                # グラフが単調に膨らみ、二時制クエリの履歴が結合の周期で埋まる**
                continue
            self._write(
                Assertion(
                    subject=entity,
                    predicate=fluent_iri,
                    object=True,
                    valid_from=at,
                    tx_from=at,
                    modality=Modality.INFERRED,
                    belief=BooleanBelief(p=posterior),
                    provenance=f"{self.ingest.provenance}/coupling/{at.isoformat()}",
                    ontology_version=self.ingest.ontology_version,
                )
            )
            written += 1
        return written

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


def graph_relations(store: GraphStore) -> RelationProvider:
    """知識グラフから強い関係を読む provider を作る。

    **関係とオントロジーの読み出しを毎周期やり直す。** キャッシュしたくなるが、再接地や
    オントロジーの版上げで関係が変わったときに古い構造で結合し続けることになる。結合の周期は
    既定 60 秒で、クエリ 3 本はその中では十分に軽い。

    **集約規則が無いフルーエントは結合しない。** 既定値を埋めると、宣言忘れが
    「全部分が成立して初めて」という強い仮定として静かに効く（``knowledge.relations`` 参照）。

    同一 Situation の結合（M8-07）は**集約規則を要求しない**。「同じ出来事に参加しているから
    状態が揃いやすい」という弱い対称な制約で、all / any のような向きを持たないためである。
    強さの既定（0.3）が部分−全体（0.8）より弱いのも同じ理由。

    Args:
        store: グラフストア。

    Returns:
        ``事前 -> (PartWhole の列, SituationLink の列)``。
    """

    def provide(
        priors: Mapping[tuple[str, str], float],
    ) -> tuple[list[PartWhole], list[SituationLink]]:
        entities = [entity for entity, _fluent in priors]
        # 事前を持っている実体だけに絞る（propagate は全実体の事前を要求する）
        grouped = group_by_whole(part_whole_edges(store), known_entities=entities)
        rules = aggregation_rules(store) if grouped else {}

        couplings: list[PartWhole] = []
        for (entity, fluent), _prior in priors.items():
            parts = grouped.get(entity)
            if not parts:
                continue
            rule_iri = rules.get(fluent)
            if rule_iri is None:
                continue
            # 部分側の事前も必要（propagate の coverage 検査）
            covered = tuple(part for part in parts if (part, fluent) in priors)
            if not covered:
                continue
            couplings.append(
                PartWhole(
                    whole=entity,
                    parts=covered,
                    fluent=fluent,
                    rule=AggregationRule.from_iri(rule_iri),
                )
            )

        # 同一 Situation の結合（M8-07）。**事前を持つ参加者が 2 件以上**のものだけを作る
        # ——1 件では結合にならず、`SituationLink` の構築が例外になる
        known = set(priors)
        fluents = {fluent for _entity, fluent in priors}
        links: list[SituationLink] = []
        for situation, members in situation_members(store).items():
            for fluent in sorted(fluents):
                covered = tuple(member for member in members if (member, fluent) in known)
                if len(covered) < 2:
                    continue
                links.append(SituationLink(situation=situation, members=covered, fluent=fluent))
        return couplings, links

    return provide


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
    from sbp.knowledge import (  # noqa: PLC0415
        AssertionStore,
        ContributionStore,
        HttpGraphStore,
        MetricStore,
    )

    resolved = settings or Settings()
    store = HttpGraphStore(resolved)
    graph = AssertionStore(store, site_id=site_id)

    connection = await psycopg.AsyncConnection.connect(resolved.pg_dsn, autocommit=True)
    worker = BeliefWorker(
        resolved,
        sink=graph.assert_,
        relations=graph_relations(store),
        metrics=MetricRecorder(site=site_id, sink=MetricStore(connection, site=site_id).write),
        plans=GraphPlans(graph),
        contributions=ContributionStore(connection, site=site_id).write,
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
