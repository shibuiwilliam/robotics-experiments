"""信念更新ワーカー（M5-08）。

プローブ由来のイベント・業務記録・計画を読み、実体単位の前向きフィルタを進めて ``inferred``
アサーションを書く。実体間結合（loopy BP）は ``bp_period_s`` ごとに回す。

**結合を毎ループ回さない。** 実体間のメッセージパッシングは辺数に比例して重く、1 件のイベント
ごとに回すと処理が追いつかない。設計書 §8.1 が「一定周期で回す」と言っているのはこのためで、
周期は ``Settings.reasoning.bp_period_s``。

このモジュールは**配線だけ**を持つ。信念更新の中身は ``sbp.reasoning.belief``、結合は
``sbp.reasoning.coupling`` にある（ワーカーにロジックを書くと、テストが Compose 必須になる）。
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
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import datetime

    from sbp.core.assertion import Assertion
    from sbp.core.events import EventEnvelope

    EventSource = Callable[[int], Awaitable[Sequence[EventEnvelope]]]
    """``(最大件数) -> イベント列``。consumer の差し替えを容易にするため関数で受ける。"""
    AssertionSink = Callable[[Assertion], object]
    """アサーションを 1 件書く。戻り値は使わない（``assert_`` は ID を返す）。"""

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
        source: EventSource | None = None,
        sink: AssertionSink | None = None,
        ingest: BeliefIngest | None = None,
    ) -> None:
        """Args:
        settings: 設定（既定は環境から読む）。
        source: イベントの取得（``None`` なら取り込まない。設定漏れは起動時に警告する）。
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
        self._source = source
        self._sink = sink
        self.ingest = ingest if ingest is not None else default_ingest(self.settings)
        if source is None:
            log.warning(
                "worker.no_event_source",
                worker=WORKER_NAME,
                detail="source が無いのでイベントを取り込まない（周期処理だけが動く）",
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

    async def step(self) -> int:
        """1 ループ。イベントを 1 バッチ処理し、必要なら結合を回す。

        **イベントが取れなくても結合の周期判定は進める。** 取れた件数で早期 return すると、
        イベントが止まっている間は結合も止まり、「静かに何もしていない」状態になる。

        Returns:
            信念を更新した件数（取り込んだイベント数ではない。入れなかったイベントは
            ``ingest.skipped`` に理由別で積まれる）。
        """
        batch = await self._poll()
        outcomes = self.ingest.handle_batch(batch)
        applied = [outcome for outcome in outcomes if outcome.applied]
        for outcome in applied:
            assert outcome.assertion is not None  # applied が真なら必ずある
            self._write(outcome.assertion)

        at = now()
        if self.coupling_due(at=at):
            self.mark_coupling_done(at=at)
            log.info(
                "worker.coupling_cycle",
                worker=WORKER_NAME,
                period_s=self.coupling_period.total_seconds(),
                tracked=self.ingest.tracked_entities,
            )
        if batch:
            log.info(
                "worker.belief_batch",
                worker=WORKER_NAME,
                received=len(batch),
                applied=len(applied),
                skipped=dict(self.ingest.skipped),
            )
        return len(applied)

    async def _poll(self) -> list[EventEnvelope]:
        """イベントを 1 バッチ取る。

        ``source`` が無ければ空を返す。**空の成功として振る舞うのはここだけ**で、
        その事実は起動時に警告として出している（設定漏れを静かに通さない）。
        """
        if self._source is None:
            return []
        return list(await self._source(self.settings.reasoning.batch_size))

    def _write(self, assertion: Assertion) -> None:
        """アサーションを書く。``sink`` が無ければ書かない（dry 相当）。"""
        if self._sink is None:
            return
        self._sink(assertion)

    async def run(self, *, max_iterations: int | None = None) -> int:
        """起動する。

        Args:
            max_iterations: テスト用の上限。
        """
        loop = asyncio.get_running_loop()
        self.runtime.install_signal_handlers(loop)
        return await self.runtime.run(self.step, max_iterations=max_iterations)


def main() -> int:
    """エントリポイント。"""
    worker = BeliefWorker()
    asyncio.run(worker.run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
