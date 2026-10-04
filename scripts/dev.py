"""API とワーカーをローカルプロセスで起動する（``make dev``、M8-13）。

CLAUDE.md のコマンド表が「API（FastAPI, :8000）とワーカーをローカルプロセスで起動
（ホットリロード）」と定めているもの。**これまで存在しなかった**（``docs/PROGRESS.md``
所見 68）。

**API だけホットリロードする。** uvicorn の ``--reload`` はファイル監視のための親プロセスを
持つ形で、ワーカーを同じ仕組みに載せると「どのプロセスが再起動したか」が追えなくなる。
ワーカーは子プロセスとして起こし、落ちたらログに出して**全体を止める**——1 つ死んだまま
残りが動いていると、「イベントが流れているのに信念が増えない」状態を静かに作る。

**既定ではワーカーを起こさない。** 設定が済んでいないワーカー（接地はプローブの IRI が必要、
スケジューラはキューに書く側が未実装）を起こすと、警告だけ出して何もしないプロセスが並ぶ。
``--workers`` で明示的に選ぶ。

使い方::

    make dev                      # API だけ（:8000、ホットリロード）
    uv run python -m scripts.dev --workers belief,reconcile
    uv run python -m scripts.dev --no-reload --port 8001
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
from typing import TYPE_CHECKING

from sbp.core.config import Settings
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

__all__ = ["APP_FACTORY", "RELOAD_DIRS", "WORKERS", "main", "parse_args", "selected_workers"]

APP_FACTORY = "sbp.api.factory:build_app"
"""uvicorn に渡す組み立て関数（ホットリロードは import 文字列を要求する）。"""

WORKERS: dict[str, str] = {
    "bridge": "services.bridge_worker",
    "belief": "services.belief_worker",
    "reconcile": "services.reconcile_worker",
    "scheduler": "services.scheduler_worker",
}
"""``--workers`` で選べるワーカー（名前 → モジュール）。"""

RELOAD_DIRS = ("src", "services")
"""ホットリロードの監視対象。``tests`` を入れない（テストの編集で API が再起動する）。"""


def parse_args(argv: list[str]) -> argparse.Namespace:
    """引数を解釈する。

    Args:
        argv: コマンドライン引数（プログラム名を除く）。
    """
    parser = argparse.ArgumentParser(
        prog="scripts.dev", description="API とワーカーをローカルで起動する"
    )
    parser.add_argument("--host", default="127.0.0.1", help="API の待受アドレス")
    parser.add_argument("--port", type=int, default=8000, help="API の待受ポート")
    parser.add_argument(
        "--no-reload", action="store_true", help="ホットリロードを切る（本番に近い起動）"
    )
    parser.add_argument(
        "--workers",
        default="",
        help=f"同時に起こすワーカー（カンマ区切り。選べるのは {','.join(WORKERS)}）",
    )
    return parser.parse_args(argv)


def selected_workers(spec: str) -> list[str]:
    """``--workers`` の指定を解釈する。

    Args:
        spec: カンマ区切りの名前。

    Returns:
        ワーカー名の列。

    Raises:
        SystemExit: 未知の名前が含まれる場合。**黙って飛ばさない**——綴り間違いで
            「起こしたつもりのワーカーが動いていない」状態になる。
    """
    names = [part.strip() for part in spec.split(",") if part.strip()]
    unknown = [name for name in names if name not in WORKERS]
    if unknown:
        sys.stderr.write(
            f"unknown worker(s): {', '.join(unknown)}; choose from {', '.join(WORKERS)}\n"
        )
        raise SystemExit(2)
    return names


def _start_workers(names: list[str]) -> list[tuple[str, subprocess.Popen[bytes]]]:
    """ワーカーを子プロセスとして起こす。

    Args:
        names: ワーカー名の列。

    Returns:
        ``(名前, プロセス)`` の列。
    """
    started: list[tuple[str, subprocess.Popen[bytes]]] = []
    for name in names:
        module = WORKERS[name]
        process = subprocess.Popen([sys.executable, "-m", module])
        started.append((name, process))
        log.info("dev.worker_started", worker=name, module=module, pid=process.pid)
    return started


def _watch_workers(
    started: list[tuple[str, subprocess.Popen[bytes]]], stop: threading.Event
) -> None:
    """ワーカーの死活を監視し、1 つでも落ちたら ``stop`` を立てる。

    **1 つ死んだまま残りを動かさない。** 接地が止まったまま信念更新だけ動いていると、
    「イベントが流れているのに信念が増えない」状態を静かに作る。

    Args:
        started: 起こしたワーカー。
        stop: 停止の合図。
    """
    while not stop.wait(1.0):
        for name, process in started:
            code = process.poll()
            if code is not None:
                log.error(
                    "dev.worker_exited",
                    worker=name,
                    pid=process.pid,
                    returncode=code,
                    detail="ワーカーが落ちた。残りを動かし続けると静かな欠落になるので止める",
                )
                stop.set()
                return


def _terminate(started: list[tuple[str, subprocess.Popen[bytes]]]) -> None:
    """ワーカーを SIGTERM で止める（graceful shutdown に任せる）。

    Args:
        started: 起こしたワーカー。
    """
    for name, process in started:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            log.info("dev.worker_stopping", worker=name, pid=process.pid)
    for _name, process in started:
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


def main(argv: list[str] | None = None) -> int:
    """API（と任意のワーカー）を起動する。

    Args:
        argv: コマンドライン引数（既定は ``sys.argv[1:]``）。
    """
    import uvicorn  # noqa: PLC0415 — 起動時だけ必要

    args = parse_args(sys.argv[1:] if argv is None else argv)
    settings = Settings()
    names = selected_workers(args.workers)

    log.info(
        "dev.starting",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        workers=names,
        site_id=settings.site_id,
        env=settings.env,
    )
    if settings.env == "dev":
        log.warning(
            "dev.open_principal",
            detail=(
                "SBP_ENV=dev なのでヘッダ X-SBP-Principal で要求者を名乗れる。"
                "**認証ではない**のでローカル以外に出さない（ADR-0008 の未決）"
            ),
        )

    started = _start_workers(names)
    stop = threading.Event()
    watcher: threading.Thread | None = None
    if started:
        watcher = threading.Thread(target=_watch_workers, args=(started, stop), daemon=True)
        watcher.start()

    try:
        uvicorn.run(
            APP_FACTORY,
            factory=True,
            host=args.host,
            port=args.port,
            reload=not args.no_reload,
            reload_dirs=list(RELOAD_DIRS) if not args.no_reload else None,
            log_level=settings.log_level.lower(),
        )
    finally:
        stop.set()
        _terminate(started)
        if watcher is not None:
            watcher.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
