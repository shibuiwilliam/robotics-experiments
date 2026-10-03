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

from sbp.core.config import Settings
from sbp.core.logging import get_logger
from sbp.core.time import now
from services.runtime import ReadinessCheck, WorkerRuntime

if TYPE_CHECKING:
    from datetime import datetime

log = get_logger(__name__)

WORKER_NAME = "belief-worker"


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

    def __init__(self, settings: Settings | None = None) -> None:
        """Args:
        settings: 設定（既定は環境から読む）。
        """
        self.settings = settings or Settings()
        self.runtime = WorkerRuntime(
            name=WORKER_NAME,
            settings=self.settings,
            checks=readiness_checks(self.settings),
        )
        self._last_coupling: datetime | None = None

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

        実際のイベント消費と信念更新の配線は M7 の運用タスクで入れる。現時点では**周期の管理と
        停止の振る舞いだけ**を実装し、処理件数 0 を返す。中身を入れる前にループの性質
        （停止で落ちない、コミットする、結合を毎回回さない）をテストで固めておく。
        """
        at = now()
        if self.coupling_due(at=at):
            self.mark_coupling_done(at=at)
            log.info(
                "worker.coupling_cycle",
                worker=WORKER_NAME,
                period_s=self.coupling_period.total_seconds(),
            )
        return 0

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
