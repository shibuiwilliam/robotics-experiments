"""``make train-probe`` — 弱ラベルからプローブを学習し、ゲートを通ったら置き場に置く。

M8-23、ADR-0024。業務の主張イベント（``BusinessEvent/<フルーエント>``）を Kafka から
**新しい consumer group で最初から**読み切り、M8-24 のエンコードジョブが潜在索引に書いた潜在と
時間で突き合わせる。**真値は読まない**（不変条件 10）。

- 昇格ゲート（AUROC・ECE・整合残差の AND）を通らなければ**束を置かず**、終了コード 1 で終わる。
  ``probe_auroc`` / ``probe_ece`` は通らなくても記録する（落ちた理由を見えるようにする）。
- 置き場には**上書きしない**。同じ版を学び直すときは新しい版の ``--probe-id`` を渡す。
- 使う WM 版は ``SBP_BRIDGE_WM_VERSION``。既定値を置かない。
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from typing import TYPE_CHECKING

import numpy as np
import psycopg

from sbp.bridge.serialization import artifact_key, dumps
from sbp.bridge.training import EntitySeries, TrainingOutcome, bags_from_claims, train_probe
from sbp.core.config import Settings, get_settings
from sbp.core.errors import ConfigError
from sbp.core.events import EventEnvelope, EventLayer, topic_name
from sbp.core.logging import configure_logging, get_logger
from sbp.core.metrics import MetricSample, metric
from sbp.core.time import now
from sbp.eventlog.consumer import ConsumerPolicy, KafkaEventConsumer
from sbp.eventlog.idempotency import InMemoryIdempotencyStore
from sbp.knowledge import LatentIndex, MetricStore, S3ArtifactStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.knowledge.artifacts import ArtifactStore

__all__ = ["main", "run_training", "train_and_store"]

log = get_logger(__name__)

_MAX_LATENTS = 200_000
"""1 実体あたり読む潜在の上限（無制限に読まない）。"""


def train_and_store(
    settings: Settings,
    events: Sequence[EventEnvelope],
    series: Sequence[EntitySeries],
    *,
    store: ArtifactStore,
    fluent: str,
    probe_id: str,
    seed: int,
) -> tuple[TrainingOutcome, list[MetricSample]]:
    """弱ラベルから学習し、昇格したら束を置く。

    Args:
        settings: 設定（``SBP_BRIDGE_WM_VERSION`` が要る）。
        events: 主張イベント。
        series: 実体ごとの潜在の時系列。
        store: 成果物の置き場。
        fluent: 接地するフルーエントの型 IRI。
        probe_id: 登録するプローブの IRI（版を含む）。
        seed: 乱数シード。

    Returns:
        ``(結果, 記録する指標)``。昇格しなければ束は置かれない。

    Raises:
        ConfigError: WM 版が未設定の場合。
        ArtifactImmutableError: その版の束が既に置かれている場合。
    """
    wm_version = settings.bridge.wm_version
    if not wm_version:
        raise ConfigError("SBP_BRIDGE_WM_VERSION is required: probes are bound to a latent space")
    bags, _unused = bags_from_claims(events, fluent=fluent, settings=settings.bridge)
    outcome = train_probe(
        series,
        bags,
        probe_id=probe_id,
        grounds=fluent,
        wm_version=wm_version,
        level="mid",
        settings=settings.bridge,
        seed=seed,
    )
    samples: list[MetricSample] = []
    if outcome.metrics is not None:
        at = now()
        labels = {"probe": probe_id, "wm_version": wm_version}
        samples = [
            MetricSample(metric("probe_auroc"), outcome.metrics.auroc, at, labels),
            MetricSample(metric("probe_ece"), outcome.metrics.ece, at, labels),
        ]
    if outcome.bundle is not None:
        key = artifact_key(outcome.spec)
        store.put(key, dumps(outcome.bundle))
        log.info("train_probe.stored", key=key, probe_id=probe_id)
    return outcome, samples


async def _read_claims(settings: Settings, *, idle_s: float) -> list[EventEnvelope]:
    """主張イベントを最初から読み切る（運用の consumer group には触れない）。"""
    collected: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        collected.append(event)

    topic = topic_name(settings.site_id, EventLayer.INTEGRATION, "business")
    consumer = KafkaEventConsumer(
        settings,
        topics=(topic,),
        policy=ConsumerPolicy(consumer_group=f"train-probe-{uuid.uuid4().hex[:8]}"),
        store=InMemoryIdempotencyStore(),
        handler=collect,
    )
    async with consumer:
        await consumer.run(stop_when_idle_s=idle_s)
    return collected


async def run_training(
    settings: Settings, fluent: str, probe_id: str, seed: int, idle_s: float
) -> int:
    events = await _read_claims(settings, idle_s=idle_s)
    entities = sorted({e.subject for e in events if e.type.endswith(f"/{fluent}")})
    async with await psycopg.AsyncConnection.connect(
        settings.pg_dsn, autocommit=True
    ) as connection:
        index = LatentIndex(connection)
        series: list[EntitySeries] = []
        for entity in entities:
            rows = await index.since(
                entity=entity,
                wm_version=settings.bridge.wm_version,
                level="mid",
                limit=_MAX_LATENTS,
            )
            if rows:
                series.append(
                    EntitySeries(
                        entity,
                        tuple(at for at, _v in rows),
                        np.vstack([v for _a, v in rows]).astype(np.float64),
                    )
                )
        outcome, samples = train_and_store(
            settings,
            events,
            series,
            store=S3ArtifactStore.from_settings(settings),
            fluent=fluent,
            probe_id=probe_id,
            seed=seed,
        )
        if samples:
            await MetricStore(connection, site=settings.site_id).write(samples)
    log.info(
        "train_probe.done",
        promoted=outcome.bundle is not None,
        bags=outcome.bags,
        entities=len(series),
        reasons=list(outcome.reasons),
    )
    return 0 if outcome.bundle is not None else 1


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。"""
    parser = argparse.ArgumentParser(description="弱ラベルからプローブを学習して置き場に置く")
    parser.add_argument("--fluent", required=True, help="接地するフルーエントの型 IRI")
    parser.add_argument("--probe-id", required=True, help="プローブの IRI（版を含む）")
    parser.add_argument("--seed", type=int, required=True, help="乱数シード")
    parser.add_argument("--idle", type=float, default=5.0, help="読み切りと見なす無音の秒数")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    return asyncio.run(run_training(settings, args.fluent, args.probe_id, args.seed, args.idle))


if __name__ == "__main__":
    raise SystemExit(main())
