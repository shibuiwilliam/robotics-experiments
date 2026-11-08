"""プローブ束を置き場に入れる経路の端から端まで（M8-23-4、ADR-0024）。

運用のコードで通す: スプール → ``train_from_spool`` → ``encode_once``（潜在索引）→
業務の主張イベントを Redpanda に置く →
``run_training``（Kafka を読み切り、潜在と突き合わせ、ゲート、置き場）→
``load_probe_set``（bridge-worker が使う読み口）。外界の模擬は観測窓と業務の記録の台本だけ。

確かめること:

1. ゲートを通った束が置き場に置かれ、bridge-worker の ``load_probe_set`` で読める。
2. **ゲートを通らなければ束は置かれない**（終了コード 1、置き場は空のまま）。
3. 同じ版の束は上書きしない。
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import suppress
from datetime import UTC, timedelta
from typing import TYPE_CHECKING

import numpy as np
import psycopg
import pytest
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic
from scripts.encode import encode_once
from scripts.train_koopman import train_from_spool
from scripts.train_probe import run_training
from services.bridge_worker import load_probe_set

from sbp.core import vocab
from sbp.core.belief import BooleanBelief
from sbp.core.config import BridgeSettings, Settings
from sbp.core.errors import ArtifactImmutableError, ArtifactNotFoundError
from sbp.core.events import (
    EventEnvelope,
    EventLayer,
    Producer,
    RecordEvidence,
    ValidTime,
    topic_name,
)
from sbp.core.ids import stable_event_id
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.eventlog.producer import KafkaEventProducer
from sbp.knowledge import S3ArtifactStore
from sbp.knowledge.artifacts import LocalArtifactStore
from sbp.testing import SynthConfig, SynthWorld
from sbp.testing.synth import Fluent

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime
    from pathlib import Path

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

FLUENT = "https://sbp.example/ent#Running"
MODEL = "https://sbp.example/model/probe-e2e-koopman"


def _claim(entity: str, at: datetime, p: float) -> EventEnvelope:
    return EventEnvelope(
        event_id=stable_event_id(at, f"{entity}|{at.isoformat()}|{p}"),
        type=f"{vocab.core.BusinessEvent}/{FLUENT}",
        subject=entity,
        valid_time=ValidTime(start=at, uncertainty_s=0.0),
        tx_time=at,
        modality=Modality.OBSERVED,
        belief=BooleanBelief(p=p),
        evidence=(
            RecordEvidence(record="https://sbp.example/rec/1", source="https://sbp.example/s"),
        ),
        producer=Producer(agent="test", version="0"),
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


@pytest.fixture
def topic(settings: Settings, kafka_available: None) -> Iterator[tuple[str, str]]:
    del kafka_available
    site = f"it-probe-{uuid.uuid4().hex[:6]}"
    name = topic_name(site, EventLayer.INTEGRATION, "business")
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
    for future in admin.create_topics(
        [NewTopic(name, num_partitions=1, replication_factor=1)]
    ).values():
        future.result(timeout=20)
    yield site, name
    for future in admin.delete_topics([name]).values():
        with suppress(Exception):
            future.result(timeout=20)


async def _prepare(
    settings: Settings,
    site: str,
    topic_: str,
    tmp_path: Path,
    *,
    bridge: BridgeSettings | None = None,
    with_actions: bool = False,
) -> tuple[Settings, str]:
    """学習済み WM → 潜在索引 → 主張イベント。戻りは (設定, プローブ IRI)。"""
    stamp = int(time.time())
    version = f"1.0.{stamp}"
    base = bridge or BridgeSettings()
    configured = settings.model_copy(
        update={
            "site_id": site,
            "bridge": base.model_copy(
                update={"wm_version": f"{MODEL}/{version}", "weak_label_min_half_width_s": 60.0}
            ),
        }
    )
    world = SynthWorld(
        config=SynthConfig(
            start=now().replace(microsecond=0) - timedelta(hours=9),
            duration=timedelta(hours=8),
            cycles=8,
        ),
        seed=31,
    )
    entity = f"https://sbp.example/site/{site}/asset/P-PROBE"
    spool = tmp_path / "spool"
    spool.mkdir()
    window = {
        "entity": entity,
        "channels": list(world.channels),
        "timestamps": [at.astimezone(UTC).isoformat() for at in world.timestamps],
        "values": world.observations.tolist(),
    }
    if with_actions:
        # 現場のエッジが置く介入の列（M10-03、ADR-0036）。チャンネル 0 を時々印加する
        pulses = np.zeros((len(world.timestamps), 2))
        pulses[::9, 0] = 1.0
        window["actions"] = pulses.tolist()
    (spool / "a.jsonl").write_text(json.dumps(window) + "\n", encoding="utf-8")
    store = LocalArtifactStore(tmp_path / "wm")
    train_from_spool(configured, spool, store=store, domain="d", delays=8, rank=None, seed=31)
    async with await psycopg.AsyncConnection.connect(settings.pg_dsn, autocommit=True) as conn:
        await encode_once(configured, spool, store=store, connection=conn)

    labels = world.oracle.labels_for(Fluent.RUNNING)
    async with KafkaEventProducer(configured) as producer:
        for i in range(8, len(world.timestamps), 20):
            await producer.send(
                topic_, _claim(entity, world.timestamps[i], 0.9 if labels[i] else 0.1)
            )
    return configured, f"https://sbp.example/probe/Running/{version}"


class TestTrainProbeEndToEnd:
    @pytest.mark.asyncio
    async def test_a_promoted_probe_lands_in_the_store_and_the_worker_can_load_it(
        self, settings: Settings, topic: tuple[str, str], tmp_path: Path
    ) -> None:
        site, topic_ = topic
        configured, probe_id = await _prepare(settings, site, topic_, tmp_path)
        code = await run_training(configured, FLUENT, probe_id, 5, 3.0)
        assert code == 0, "合成の世界ではゲートを通る"

        loaded = load_probe_set(S3ArtifactStore.from_settings(configured), probe_ids=[probe_id])
        assert loaded.wm_version == configured.bridge.wm_version
        assert len(loaded) == 1

        with pytest.raises(ArtifactImmutableError):
            await run_training(configured, FLUENT, probe_id, 5, 3.0)

    @pytest.mark.asyncio
    async def test_failing_the_gate_places_nothing(
        self, settings: Settings, topic: tuple[str, str], tmp_path: Path
    ) -> None:
        site, topic_ = topic
        configured, probe_id = await _prepare(
            settings, site, topic_, tmp_path, bridge=BridgeSettings(promote_max_ece=0.0)
        )
        assert await run_training(configured, FLUENT, probe_id, 5, 3.0) == 1
        with pytest.raises(ArtifactNotFoundError):
            load_probe_set(S3ArtifactStore.from_settings(configured), probe_ids=[probe_id])
