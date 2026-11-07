"""``make forecast`` — フルーエントの成立確率を予測し、期限の来た予測を採点する（M9-02、ADR-0032）。

1 周期の流れ: (1) 実体の最新の潜在を起点に WM をロールアウトし、プローブを通して
先行時間ごとの予測を作る → 予測の表（区間・標本つき）と ``predicted`` のアサーション（平均）に書く。
(2) 地平が過ぎた未採点の予測を、そのときの信念と突き合わせて CRPS・被覆率にし、指標に書く。

- WM は ``SBP_BRIDGE_WM_VERSION``、プローブは ``--probe-id``（版を含む IRI、複数可）。
  ``latest`` は使えない。
- 同じ起点の予測は 2 度書かない（再実行は何も増やさない）。
- ``--watch N`` で N 秒ごとに繰り返す。乱数シードは必須。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np
import psycopg
from services.bridge_worker import load_probe_set

from sbp.core import vocab
from sbp.core.config import Settings, get_settings
from sbp.core.errors import ConfigError
from sbp.core.logging import configure_logging, get_logger
from sbp.core.time import now
from sbp.knowledge import (
    AssertionStore,
    ForecastStore,
    HttpGraphStore,
    LatentIndex,
    MetricStore,
    S3ArtifactStore,
)
from sbp.reasoning.forecasting import forecast_assertions, make_forecast, score_due
from sbp.worldmodel.base import LatentLevel, LatentState
from sbp.worldmodel.serialization import load_model
from scripts._wm import split_wm_version

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.bridge.runner import ProbeSet
    from sbp.worldmodel.families.koopman import KoopmanModel

__all__ = ["forecast_once", "main"]

log = get_logger(__name__)

_LEVEL = LatentLevel.MID


async def forecast_once(
    settings: Settings,
    *,
    entities: Sequence[str],
    fluents: Sequence[str],
    model: KoopmanModel,
    probes: ProbeSet,
    seed: int,
    connection: psycopg.AsyncConnection[tuple[object, ...]],
    store: AssertionStore,
) -> tuple[int, int]:
    """1 周期ぶん: 予測を作って書き、期限の来た予測を採点する。

    Args:
        settings: 設定（予測の地平・本数などは ``reasoning``）。
        entities: 対象実体の IRI。
        fluents: 対象フルーエント型の IRI。
        model: 学習済みの WM。
        probes: 新しい版のプローブ。
        seed: 乱数シード。
        connection: Postgres 接続。
        store: アサーションストア。

    Returns:
        ``(書いた予測の数, 採点した予測の数)``。
    """
    reasoning = settings.reasoning
    index = LatentIndex(connection)
    forecasts = ForecastStore(connection, site=settings.site_id)
    written = 0
    for entity in entities:
        latest = await index.latest(
            entity=entity, wm_version=settings.bridge.wm_version, level=_LEVEL.value
        )
        if latest is None:
            log.warning("forecast.no_latent", entity=entity)
            continue
        at, vector = latest
        dim = vector.size
        state = LatentState(
            z=vector,
            level=_LEVEL,
            at=at,
            wm_version=model.meta.version_iri,
            covariance=np.eye(dim) * reasoning.forecast_state_std**2,
        )
        for fluent in fluents:
            forecast = make_forecast(
                model=model,
                probe=probes,
                entity=entity,
                fluent=fluent,
                state=state,
                horizon_steps=reasoning.forecast_horizon_steps,
                stride=reasoning.forecast_stride,
                samples=reasoning.forecast_samples,
                interval_level=reasoning.forecast_interval_level,
                seed=seed,
                ontology_version=vocab.ONTOLOGY_VERSION,
            )
            saved = await forecasts.save(
                forecast_id=forecast.forecast_id,
                entity=entity,
                fluent=fluent,
                horizon_origin=forecast.horizon_origin,
                horizon_end=forecast.points[-1].at(forecast.horizon_origin),
                wm_version=forecast.wm_version,
                interval_level=forecast.interval_level,
                payload=forecast.model_dump(mode="json"),
            )
            if not saved:
                continue  # 同じ起点は書いた
            for assertion in forecast_assertions(forecast, tx_from=now()):
                store.assert_(assertion)
            written += 1

    current = now()
    due = await forecasts.due(before=current - timedelta(seconds=reasoning.forecast_grace_s))
    samples, scored = score_due(due, store=store, at=current)
    if samples:
        await MetricStore(connection, site=settings.site_id).write(samples)
    await forecasts.mark_scored([r.forecast_id for r in scored], at=current)
    log.info("forecast.cycle", written=written, due=len(due), scored=len(scored))
    return written, len(scored)


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    model_id, version = split_wm_version(settings.bridge.wm_version)
    artifacts = S3ArtifactStore.from_settings(settings)
    model = load_model(artifacts, model_id=model_id, version=version)
    probes = load_probe_set(artifacts, probe_ids=args.probe_id)
    if probes.wm_version != settings.bridge.wm_version:
        raise ConfigError(
            f"the probes belong to {probes.wm_version} but SBP_BRIDGE_WM_VERSION is "
            f"{settings.bridge.wm_version}; a forecast would read the latents in the wrong space"
        )
    store = AssertionStore(HttpGraphStore(settings), site_id=settings.site_id)
    async with await psycopg.AsyncConnection.connect(
        settings.pg_dsn, autocommit=True
    ) as connection:
        while True:
            await forecast_once(
                settings,
                entities=args.entity,
                fluents=args.fluent,
                model=model,
                probes=probes,
                seed=args.seed,
                connection=connection,
                store=store,
            )
            if args.watch <= 0:
                return
            await asyncio.sleep(args.watch)


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。"""
    parser = argparse.ArgumentParser(description="予測を作って書き、期限の来た予測を採点する")
    parser.add_argument("--entity", action="append", required=True, help="対象実体の IRI")
    parser.add_argument("--fluent", action="append", required=True, help="フルーエント型の IRI")
    parser.add_argument("--probe-id", action="append", required=True, help="プローブの IRI")
    parser.add_argument("--seed", type=int, required=True, help="乱数シード（必須）")
    parser.add_argument(
        "--watch", type=float, default=0.0, help="N 秒ごとに繰り返す（0 なら 1 回）"
    )
    args = parser.parse_args(argv)
    configure_logging(get_settings().log_level)
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
