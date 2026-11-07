"""``make encode`` — スプールの観測窓を潜在にして潜在索引に書く（M8-24、ADR-0023）。

推定パスの先頭（設計書 §2.5）: 観測窓 → 学習済みの WM（置き場の重み）→ 潜在索引。bridge-worker が
その索引を読む。現場のエッジが窓を ``SBP_WM_SPOOL_DIR``（JSONL）に置く。

- 使う WM は ``SBP_BRIDGE_WM_VERSION``（``<モデル IRI>/<版>``）。**既定値を置かない**——適当な版が
  既定だと、別の版の潜在を索引に書いてしまう。
- 観測値は永続化しない（潜在だけ。不変条件 9）。スプールは読み取り専用。
- 何度実行しても同じ（ウォーターマークで処理済みの時刻を飛ばす）。``--watch N`` で N 秒ごとに
  繰り返す。
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg

from sbp.core.config import Settings, get_settings
from sbp.core.logging import configure_logging, get_logger
from sbp.knowledge import LatentIndex, S3ArtifactStore, WatermarkStore
from sbp.worldmodel.encoding import EncodeReport, EncodeRunner, JsonlObservationSource
from sbp.worldmodel.serialization import load_model
from scripts._wm import split_wm_version, spool_path

if TYPE_CHECKING:
    from sbp.knowledge.artifacts import ArtifactStore

__all__ = ["WORKER_NAME", "encode_once", "main"]

log = get_logger(__name__)

WORKER_NAME = "encoder"
"""ウォーターマークの worker 名。接地ワーカー（``bridge-worker``）の読みとは別の列を持つ。"""


async def encode_once(
    settings: Settings,
    spool: Path,
    *,
    store: ArtifactStore | None = None,
    connection: psycopg.AsyncConnection[tuple[object, ...]] | None = None,
) -> EncodeReport:
    """スプールの窓を 1 回ぶん処理する。

    Args:
        settings: 設定（``SBP_BRIDGE_WM_VERSION`` が要る）。
        spool: スプールの置き場。
        store: 重みの置き場（既定は S3 互換ストア）。
        connection: Postgres 接続（既定は ``SBP_PG_DSN`` で開いて閉じる）。

    Returns:
        内訳つきの結果。

    Raises:
        ConfigError: WM 版が未設定または読めない場合。
    """
    wm_version = settings.bridge.wm_version
    model_id, version = split_wm_version(wm_version)
    artifacts = store if store is not None else S3ArtifactStore.from_settings(settings)
    model = load_model(artifacts, model_id=model_id, version=version)
    owns = connection is None
    conn = connection or await psycopg.AsyncConnection.connect(settings.pg_dsn, autocommit=True)
    try:
        runner = EncodeRunner(
            model,
            index=LatentIndex(conn),
            watermarks=WatermarkStore(
                conn, worker=WORKER_NAME, wm_version=model.meta.version_iri, level="mid"
            ),
        )
        return await runner.run(JsonlObservationSource(spool))
    finally:
        if owns:
            await conn.close()


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。"""
    parser = argparse.ArgumentParser(description="観測窓を潜在にして索引に書く")
    parser.add_argument("--spool", type=Path, default=None, help="スプールの置き場（既定は設定）")
    parser.add_argument(
        "--watch", type=float, default=0.0, help="N 秒ごとに繰り返す（0 なら 1 回）"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    spool = spool_path(settings, args.spool)

    async def run() -> None:
        while True:
            report = await encode_once(settings, spool)
            log.info(
                "encode.done",
                windows=report.windows,
                rows_written=report.rows_written,
                skipped_processed=report.skipped_processed,
                skipped_short=report.skipped_short,
                skipped_mismatch=report.skipped_mismatch,
                rejected=report.rejected,
            )
            if args.watch <= 0.0:
                return
            await asyncio.sleep(args.watch)

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
