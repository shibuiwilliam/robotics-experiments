"""ワーカー共通のランタイム（インフラ・テストルール、M5-08）。

> 1 ワーカー 1 プロセス。SIGTERM で graceful shutdown し、オフセットをコミットしてから終了する。
> 起動時に Settings と Compose の疎通を検査する。

**起動時に疎通を検査するのが要点である。** 検査せずに走り出すと、接続できないまま「処理件数 0」で
正常稼働しているように見える。最初の 1 件が来るまで誰も気付かない。

**SIGTERM で即座に落ちない。** 処理中のイベントを捨てるとオフセットだけが進み、at-least-once が
壊れる（イベントが 1 件消える）。停止要求を受けたら「いまのループを終えてから」止まる。

**KeyboardInterrupt を SIGTERM と同じ扱いにする。** 開発中に Ctrl-C で落としたときだけ別の経路を
通ると、本番の停止で起きる問題が開発中に出ない。
"""

from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

from sbp.core.errors import DependencyUnavailableError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from sbp.core.config import Settings

__all__ = ["ReadinessCheck", "WorkerRuntime", "check_readiness"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    """起動時の疎通検査 1 件。

    Attributes:
        name: 依存の名前（``redpanda`` / ``oxigraph`` など）。
        probe: 疎通を確かめる関数。偽または例外で未到達とみなす。
        required: 必須か。必須でないものは警告だけ出して続行する。
    """

    name: str
    probe: Callable[[], bool]
    required: bool = True


def check_readiness(checks: Sequence[ReadinessCheck], *, worker: str) -> None:
    """全依存の疎通を検査する。

    **必須の依存が 1 つでも落ちていたら起動しない。** 「後で繋がるかもしれない」で走り出すと、
    その間に来たイベントの扱いが曖昧になる（捨てたのか溜めたのかが分からない）。

    Args:
        checks: 検査の列。
        worker: ワーカー名（ログに出す）。

    Raises:
        DependencyUnavailableError: 必須の依存に到達できない場合。
    """
    unavailable: list[str] = []
    for check in checks:
        try:
            reachable = check.probe()
        except Exception as exc:
            log.info(
                "worker.dependency_probe_failed",
                worker=worker,
                dependency=check.name,
                error=str(exc)[:200],
            )
            reachable = False
        if reachable:
            log.info("worker.dependency_ready", worker=worker, dependency=check.name)
            continue
        if check.required:
            unavailable.append(check.name)
        else:
            log.info("worker.optional_dependency_missing", worker=worker, dependency=check.name)
    if unavailable:
        raise DependencyUnavailableError(
            f"{worker} cannot start: {', '.join(unavailable)} unreachable. Starting anyway would "
            "look like healthy idling while events pile up with no one consuming them"
        )


@dataclass(slots=True)
class WorkerRuntime:
    """1 ワーカーの実行ループと graceful shutdown。

    Attributes:
        name: ワーカー名。
        settings: 設定。
        checks: 起動時の疎通検査。
        stop: 停止要求のフラグ。
        processed: 処理したループ回数。
    """

    name: str
    settings: Settings
    checks: tuple[ReadinessCheck, ...] = ()
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    processed: int = 0

    def request_stop(self, *, reason: str = "signal") -> None:
        """停止を要求する（いまのループを終えてから止まる）。

        Args:
            reason: 理由（ログに出す）。
        """
        if not self.stop.is_set():
            log.info("worker.stop_requested", worker=self.name, reason=reason)
            self.stop.set()

    def install_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        """SIGTERM / SIGINT を停止要求に繋ぐ。

        Args:
            loop: イベントループ。
        """
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                # add_signal_handler はキーワード引数を渡せないので部分適用する
                loop.add_signal_handler(sig, partial(self.request_stop, reason=sig.name))
            except NotImplementedError:  # pragma: no cover — Windows など
                log.info("worker.signal_handler_unavailable", worker=self.name, signal=sig.name)

    async def run(
        self,
        step: Callable[[], Awaitable[int]],
        *,
        idle_sleep_s: float = 0.5,
        max_iterations: int | None = None,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> int:
        """停止要求が来るまで ``step`` を回す。

        ``step`` は「このループで処理した件数」を返す。0 なら少し待つ——忙しくループを回して
        CPU を焼かないためで、イベントが来ていないときの待ちは正常な状態である。

        **終了時に ``commit`` を呼ぶ**（オフセットのコミット）。ここを飛ばすと、at-least-once の
        再処理が毎回の再起動で発生する。

        Args:
            step: 1 ループの処理。処理件数を返す。
            idle_sleep_s: 何も処理しなかったときの待ち時間。
            max_iterations: テスト用の上限（``None`` なら停止要求まで回す）。
            commit: 終了時に呼ぶコミット関数。

        Returns:
            処理した総件数。
        """
        check_readiness(self.checks, worker=self.name)
        log.info("worker.started", worker=self.name, checks=[check.name for check in self.checks])
        total = 0
        iterations = 0
        try:
            while not self.stop.is_set():
                if max_iterations is not None and iterations >= max_iterations:
                    break
                iterations += 1
                handled = await step()
                total += handled
                self.processed += 1
                if handled == 0:
                    # 停止要求が来ていれば待たずに抜ける（停止が遅延しないように）
                    try:
                        await asyncio.wait_for(self.stop.wait(), timeout=idle_sleep_s)
                    except TimeoutError:
                        continue
        finally:
            if commit is not None:
                # 停止要求でも例外でもコミットする。処理済みを再処理させないため
                await commit()
                log.info("worker.offsets_committed", worker=self.name)
            log.info(
                "worker.stopped",
                worker=self.name,
                iterations=iterations,
                handled=total,
                graceful=self.stop.is_set() or max_iterations is not None,
            )
        return total
