"""エピステミック・スケジューラのワーカー（M7 の運用タスク）。

照合が出した追加観測の要求（``ObservationRequest``）を溜め、周期ごとに効用
``Σ v(x)·EIG(x) − cost`` でバッチを選び、観測クエリを発する（設計書 §8.7）。

**このワーカーは観測を実行しない。** クエリを発するところまでで、実際にセンサを動かす・人に
聞く・業務システムを引くのは別の経路である。結果は ``observed`` のイベントとして戻ってくる
（`.claude/rules/reasoning-actions.md`: 「実行結果は ``observed`` で戻る」）。
**実行と選択を同じワーカーに入れない**のは、実行手段ごとに失敗の仕方と再試行の性質が違い、
スケジューラの選択ロジックがそれに引きずられるためである。

**人への質問には頻度上限がある**（``max_human_queries_per_hour``）。劣モジュラ性では止まらない
——安いセンサ測定をもう 1 回するのは実際に少し情報を増やすので効用は正のまま残る（所見 26）。
重複質問を止めるのは頻度上限の役目である。

**選ばなかった要求を捨てない。** 次の周期でまた候補になる。捨てると、一度コストが高いと
判定された観測が永久に実行されない（信念が変わって事前が動けば効用は変わる）。

**要求は作業キューから取る**（ADR-0011）。イベントログには載せられない——封筒は「誰かの主張」を
運ぶ形で、追加観測の要求は主張ではなく指令であり、様相格子に席が無い。観測手段は
``observation/<domain>.yaml`` の宣言から引く（``sbp.reasoning.means.MeansRegistry``）。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from sbp.core.config import Settings
from sbp.core.logging import get_logger
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.reasoning.means import MeansRegistry
from sbp.reasoning.requests import ObservationMeans, to_queries
from sbp.reasoning.scheduler import HumanQueryLog, QueryKind, SchedulerBudget, schedule
from services.runtime import ReadinessCheck, WorkerRuntime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import datetime

    from sbp.knowledge.observations import ObservationQueue
    from sbp.reasoning.reconciliation.learning import ObservationRequest
    from sbp.reasoning.scheduler import ObservationQuery

    RequestSource = Callable[[], Awaitable[Sequence["ObservationRequest"]]]
    """溜まっている追加観測の要求を返す（作業キューから読むので非同期）。"""
    PriorSource = Callable[[Sequence[tuple[str, str]]], Awaitable[Mapping[tuple[str, str], float]]]
    """``[(実体, フルーエント)] -> 現在の信念``。**推測で埋めないためにこれが要る。**

    **要る鍵を渡す形にしてある。** 全信念を返す形にすると、要求が 1 件のときも知識グラフから
    全実体の信念を引くことになる（拠点に設備が数百台あれば毎周期それを走る）。
    """
    QuerySink = Callable[["ObservationQuery"], object]
    """観測クエリ 1 件を発する。"""
    IssuedHook = Callable[["ObservationQuery"], object]
    """発行を記録する（作業キューの ``issued_at`` を立てる）。"""
    CommitHook = Callable[[], Awaitable[None]]
    """終了時のコミット（このワーカーは consumer を持たないので通常は不要）。"""

log = get_logger(__name__)

WORKER_NAME = "scheduler-worker"

_FALLBACK_MEANS = ObservationMeans(kind=QueryKind.SAMPLING_RATE, reliability=0.95, cost=0.1)
"""宣言を読めないときの最後の手段（サンプリング率の変更）。

通常は ``observation/<domain>.yaml`` の ``default`` を使う（``MeansRegistry``）。これが要るのは
単体テストと、宣言を渡さずに構築した場合である。

**人への質問を既定にしない。** 既定にすると頻度上限に当たって何も選ばれなくなる周期が出るし、
「自動で人に聞き続ける」挙動は望ましくない。サンプリング率の変更は最も安く、帯域だけを使う。
"""


def readiness_checks(settings: Settings) -> tuple[ReadinessCheck, ...]:
    """このワーカーの依存。

    **イベントログに依存しない。** 要求は呼び出し側から受け取り、クエリは sink へ出す。
    知識グラフは事前確率の取得に使うが、それも provider 経由なのでここでは検査しない
    ——provider が何を使うかはこのワーカーの関心ではない。

    Args:
        settings: 設定。
    """
    from services.belief_worker import http_reachable  # noqa: PLC0415 — 疎通検査を共有する

    return (
        ReadinessCheck(name="oxigraph", probe=lambda: http_reachable(settings.sparql_query_url)),
    )


class SchedulerWorker:
    """周期的な観測計画。

    Attributes:
        settings: 設定。
        runtime: 実行ループ。
        human_log: 人への質問の記録（頻度上限の判定に使う）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        requests: RequestSource | None = None,
        priors: PriorSource | None = None,
        sink: QuerySink | None = None,
        means: MeansRegistry | None = None,
        issued: IssuedHook | None = None,
    ) -> None:
        """Args:
        settings: 設定（既定は環境から読む）。
        requests: 溜まっている要求（``None`` なら何も計画しない）。
        priors: 現在の信念（``None`` なら**事前が無いので 1 件も変換できない**）。
        sink: 観測クエリの出力（``None`` なら発しない）。
        means: フルーエントごとの観測手段（``None`` なら全件に ``_FALLBACK_MEANS``）。
        issued: 発行を記録するフック（``None`` なら記録しない）。作業キューの
            ``issued_at`` を立てるのに使う——**発行は「頼んだ」であって「分かった」ではない**
            ので、未充足のまま残る。
        """
        self.settings = settings or Settings()
        self.runtime = WorkerRuntime(
            name=WORKER_NAME,
            settings=self.settings,
            checks=readiness_checks(self.settings),
        )
        self.human_log = HumanQueryLog()
        self._requests = requests
        self._priors = priors
        self._sink = sink
        self._means = means
        self._issued = issued
        self._last_plan: datetime | None = None
        self.issued_total = 0
        self.skipped_total = 0
        if requests is None:
            log.warning(
                "worker.no_request_source",
                worker=WORKER_NAME,
                detail="requests が無いので計画する対象が無い",
            )
        if priors is None:
            log.warning(
                "worker.no_prior_source",
                worker=WORKER_NAME,
                detail="priors が無いので要求を 1 件も変換できない（事前を推測で埋めない）",
            )

    @property
    def plan_period(self) -> timedelta:
        """計画を回す周期。

        人への質問の上限が時間あたりなので、**それより細かい周期で回しても上限は増えない**。
        既定は照合の周期と同じにしてある（要求は照合から来るため）。
        """
        return timedelta(seconds=self.settings.reasoning.reconcile_period_s)

    def plan_due(self, *, at: datetime) -> bool:
        """この時点で計画を回すべきか。

        Args:
            at: 判定時刻。
        """
        if self._last_plan is None:
            return True
        return at - self._last_plan >= self.plan_period

    def mark_plan_done(self, *, at: datetime) -> None:
        """計画を回したことを記録する。

        Args:
            at: 実行時刻。
        """
        self._last_plan = at

    @property
    def budget(self) -> SchedulerBudget:
        """この周期の制約。"""
        return SchedulerBudget(
            total_cost=self.settings.reasoning.scheduler_total_cost,
            max_queries=self.settings.reasoning.scheduler_max_queries,
            max_human_queries=self.settings.reasoning.max_human_queries_per_hour,
            window=timedelta(hours=1),
        )

    async def step(self) -> int:
        """1 ループ。周期が来たら計画して発する。

        Returns:
            発した観測クエリの件数。
        """
        at = now()
        if not self.plan_due(at=at):
            return 0
        self.mark_plan_done(at=at)
        return await self._plan(at=at)

    async def _plan(self, *, at: datetime) -> int:
        """要求を変換し、選び、発する。

        Args:
            at: 計画時刻。

        Returns:
            発した件数。
        """
        if self._requests is None or self._priors is None:
            return 0
        pending = list(await self._requests())
        if not pending:
            return 0

        # 要る事前だけを引く（全信念を引くと実体数に比例して重くなる）
        keys = [(request.entity, request.fluent) for request in pending]
        priors = await self._priors(keys)

        # **手段はフルーエントごとに引く。** 全件に同じ手段を当てると、人の確認でしか
        # 確かめられないフルーエントも「サンプリング率を上げれば分かる」ことになり、
        # 信頼度とコストが実態から外れる（ADR-0011）
        queries, skipped = to_queries(pending, priors=priors, means=self._means_for)
        self.skipped_total += len(skipped)
        if not queries:
            log.info(
                "worker.no_convertible_requests",
                worker=WORKER_NAME,
                pending=len(pending),
                skipped=len(skipped),
                detail="事前確率が無いので変換できない。信念が入っていない可能性",
            )
            return 0

        decision = schedule(
            queries,
            budget=self.budget,
            # 窓内の質問時刻だけを渡す（`schedule` が窓で再度絞るので全件でも動くが、
            # 履歴が伸び続けると毎周期の走査が重くなる）
            recent_human_queries=self.human_log.asked_at,
            at=at,
        )
        for query in decision.chosen:
            if query.kind.asks_a_human:
                self.human_log.record(at=at)
            self._issue(query)
        self.issued_total += len(decision.chosen)

        log.info(
            "worker.scheduler_cycle",
            worker=WORKER_NAME,
            pending=len(pending),
            convertible=len(queries),
            chosen=len(decision.chosen),
            human=decision.human_queries,
            # **選ばなかった理由を出す。** 0 件のとき「要求が無い」のか「効用が足りない」のか
            # 「上限に当たった」のかが区別できないと、計画が機能していないことに気付けない
            explanation=decision.explain(),
        )
        return len(decision.chosen)

    def _means_for(self, fluent: str) -> ObservationMeans:
        """フルーエントの観測手段を引く。

        Args:
            fluent: フルーエント IRI。
        """
        if self._means is None:
            return _FALLBACK_MEANS
        return self._means.for_fluent(fluent)

    def _issue(self, query: ObservationQuery) -> None:
        """クエリを発し、発行を記録する。``sink`` が無ければ発しない。"""
        if self._sink is None:
            return
        self._sink(query)
        if self._issued is not None:
            self._issued(query)

    async def run(
        self, *, max_iterations: int | None = None, commit: CommitHook | None = None
    ) -> int:
        """起動する。

        Args:
            max_iterations: テスト用の上限。
            commit: 終了時のコミット。
        """
        loop = asyncio.get_running_loop()
        self.runtime.install_signal_handlers(loop)
        return await self.runtime.run(self.step, max_iterations=max_iterations, commit=commit)


async def build_worker(
    settings: Settings | None = None, *, site_id: str
) -> tuple[SchedulerWorker, ObservationQueue]:
    """実運用の配線を組む（作業キュー → 計画 → 観測クエリ）。

    **観測クエリの送信先はまだ無い。** クエリを受けて実際に観測する経路（センサ制御・人への
    通知・業務システムへの問い合わせ）は本リポジトリの範囲外である（設計書 §8.7 は手段を
    列挙するが、実行は拠点の設備に依る）。ここでは**キューに発行を記録する**ところまでで、
    ``sink`` は呼び出し側が差す。

    Args:
        settings: 設定（既定は環境から読む）。
        site_id: 拠点識別子（作業キューと信念グラフの両方を絞る）。

    Returns:
        ``(ワーカー, 作業キュー)``。
    """
    import psycopg  # noqa: PLC0415 — 実運用の配線でのみ使う

    from sbp.knowledge import AssertionStore, HttpGraphStore, ObservationQueue  # noqa: PLC0415

    resolved = settings or Settings()
    connection = await psycopg.AsyncConnection.connect(resolved.pg_dsn, autocommit=True)
    queue = ObservationQueue(connection, site=site_id)
    graph = AssertionStore(HttpGraphStore(resolved), site_id=site_id)

    async def requests() -> list[ObservationRequest]:
        """作業キューから未充足の要求を読む（``knowledge`` の行 → ``reasoning`` の型）。

        層の境界がここに出る——``knowledge`` は ``ObservationRequest`` を知らないので
        （下位層が上位層の型を import できない）、変換はワーカーの仕事である。
        """
        from sbp.reasoning.reconciliation.learning import (  # noqa: PLC0415
            ObservationRequest as Request,
        )

        rows = await queue.outstanding(limit=resolved.reasoning.batch_size)
        return [
            Request(
                entity=row.entity,
                fluent=row.fluent,
                interval=row.interval,
                reason=row.reason,
                utility=row.utility,
                discrepancy_id=row.discrepancy_id,
            )
            for row in rows
        ]

    async def priors(keys: Sequence[tuple[str, str]]) -> dict[tuple[str, str], float]:
        """要求が指す ``(実体, フルーエント)`` の現在の信念を引く。

        **``inferred`` の現在信念だけを見る。** ``planned`` を事前に使うと「計画どおりなら
        こうなっているはず」が事前になり、情報利得が計画との一致度に引きずられる。
        同期の SPARQL 呼び出しなので ``asyncio.to_thread`` に逃がす（ループを塞がない）。

        Args:
            keys: ``(実体 IRI, フルーエント IRI)`` の列。
        """

        def fetch() -> dict[tuple[str, str], float]:
            found: dict[tuple[str, str], float] = {}
            for entity, fluent in keys:
                rows = graph.query(subject=entity, predicate=fluent, modality=Modality.INFERRED)
                if rows:
                    # 同じ述語に複数の現在信念は無い想定だが、あれば最新の tx を採る
                    latest = max(rows, key=lambda row: row.tx_from)
                    found[(entity, fluent)] = latest.confidence
            return found

        return await asyncio.to_thread(fetch)

    def record_issued(query: ObservationQuery) -> object:
        """発行を作業キューに記録する。

        ``query_id`` は ``<discrepancy_id>:<fluent>`` なので、そこから鍵を戻せる
        （:meth:`ObservationQueue.request_id` と同じ組み方）。
        """
        discrepancy_id, _, fluent = query.query_id.rpartition(":")
        return asyncio.create_task(
            queue.mark_issued(
                discrepancy_id=discrepancy_id,
                fluent=fluent,
                query_id=query.query_id,
                at=now(),
            )
        )

    worker = SchedulerWorker(
        resolved,
        requests=requests,
        priors=priors,
        means=MeansRegistry.from_directory(),
        issued=record_issued,
    )
    log.info("worker.wired", worker=WORKER_NAME, site_id=site_id)
    return worker, queue


def main() -> int:
    """エントリポイント。

    要求は作業キュー（``observation_requests``）から取る（ADR-0011）。**キューに書く側は
    まだ無い**——要求は照合の解決から出るが、解決は人の判断であり、それを受けて実行する
    ワーカーは M8-08 である。それまでこのワーカーは「キューが空なので 0 件」を返す。

    観測クエリの送信先（``sink``）も設定していない。クエリを受けて実際に観測する経路は
    拠点の設備に依るため本リポジトリの範囲外である（ADR-0011 の未決）。
    """

    async def _run() -> None:
        settings = Settings()
        worker, _queue = await build_worker(settings, site_id=settings.site_id)
        await worker.run()

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
