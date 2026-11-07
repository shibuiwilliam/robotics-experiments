"""ゲート G8 の統合テスト（docs/TASK.md M8-11、ADR-0025）。

検収内容: **外界の入力だけをテストが置き、基盤の内側は運用のコードで通して、6 つのワーカーの
``step()`` がそれぞれ非ゼロを返す。**

外界（テストが模擬するもの）は 3 つだけ:

1. 物理の観測窓 — ``SynthWorld`` が作った窓を、エッジのスプール（JSONL）に置く。
2. 業務システム — SQLite のモックに、資産台帳と MES の稼働記録を台本で入れる。
3. 人の操作 — API コマンド（``resolve_discrepancy``）を呼ぶ。

内側はすべて production のコード:

    train_koopman → encode → ingest-worker → train_probe → bridge-worker → belief-worker
        → reconcile-worker → （人のコマンド）→ learning-worker → scheduler-worker

真値（オラクル）は**業務の台本を作るためにテストが使う**だけで（業務システムの言い分は真値から
作る）、基盤の側のコードには渡らない（不変条件 10）。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import suppress
from datetime import UTC, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic
from scripts.encode import encode_once
from scripts.train_koopman import train_from_spool
from scripts.train_probe import run_training
from services.belief_worker import build_worker as build_belief_worker
from services.bridge_worker import build_worker as build_bridge_worker
from services.ingest_worker import TABLES
from services.ingest_worker import build_worker as build_ingest_worker
from services.learning_worker import build_worker as build_learning_worker
from services.reconcile_worker import build_worker as build_reconcile_worker
from services.scheduler_worker import build_worker as build_scheduler_worker

from sbp.api.commands import build_command_handler
from sbp.core import vocab
from sbp.core.config import BridgeSettings, IntegrationSettings, ReasoningSettings, Settings
from sbp.core.events import EventLayer, topic_name
from sbp.core.ids import site_iri
from sbp.core.time import now
from sbp.integration.mock import MockBusinessSystem
from sbp.integration.mock.business_system import Scenario, ScenarioStep, build_default_scenario
from sbp.knowledge import (
    AssertionStore,
    DecisionQueue,
    DiscrepancyStore,
    ObservationQueue,
    S3ArtifactStore,
)
from sbp.testing import SynthConfig, SynthWorld
from sbp.testing.synth import Fluent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from datetime import datetime

    from sbp.knowledge import HttpGraphStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

DEADLINE_S = 60.0
RUNNING = str(vocab.ent.Running)
MODEL = "https://sbp.example/model/g8-koopman"
OPERATOR = "https://sbp.example/person/operator-g8"
WRONG_AT = 275
"""台本の「誤った記録」の位置。物理は停止区間（216〜359）にいるのに、MES は稼働中と言う。

**この配置は窓の割り当て規則（記録は最も近い刻みへ入る）に依存する。** 記録は刻みから
10 分離れており、同時刻の食い違いではない。割り当てを時間幅で縛る変更をしたら見直すこと。

昇格イベントの刻みは状態の変わり目（216 の停止、360 の稼働…）にしか無く、記録は最も近い刻みに入る
（後から届いたものが勝つ）。275 は刻み 216 に最も近い最後の記録なので、刻み 216 で食い違い、
次の刻み 360 で一致に戻る——食い違いは 1440 秒続く。"""


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


@pytest.fixture
def site(test_graph_suffix: str, assertion_store: AssertionStore) -> str:
    del assertion_store
    return f"{test_graph_suffix}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def topics(site: str, settings: Settings, kafka_available: None) -> Iterator[None]:
    del kafka_available
    names = [
        topic_name(site, EventLayer.INTEGRATION, "business"),
        topic_name(site, EventLayer.BRIDGE, "physical"),
        topic_name(site, EventLayer.REASONING, "discrepancy"),
    ]
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap})
    for future in admin.create_topics(
        [NewTopic(name, num_partitions=1, replication_factor=1) for name in names]
    ).values():
        future.result(timeout=20)
    yield
    for future in admin.delete_topics(names).values():
        with suppress(Exception):
            future.result(timeout=20)


async def _until(condition: Callable[[], bool], step: Callable[[], Awaitable[Any]]) -> bool:
    deadline = time.monotonic() + DEADLINE_S
    while not condition() and time.monotonic() < deadline:
        await step()
    return condition()


@pytest.fixture
async def connection(
    settings: Settings, site: str, postgres_available: None
) -> AsyncIterator[psycopg.AsyncConnection[tuple[Any, ...]]]:
    del postgres_available
    conn = await psycopg.AsyncConnection.connect(settings.pg_dsn, autocommit=True)
    yield conn
    for table in (
        "ingest_cursors",
        "business_records",
        "learning_decisions",
        "observation_requests",
        "discrepancies",
        "metrics",
    ):
        with suppress(Exception):
            await conn.execute(f"DELETE FROM {table} WHERE site = %s", (site,))  # type: ignore[arg-type]
    await conn.close()


def _business_scenario(world: SynthWorld) -> Scenario:
    """業務システムの台本（外界）。MES の言い分は真値から作り、1 件だけ誤った記録を混ぜる。"""
    labels = world.oracle.labels_for(Fluent.RUNNING)
    t0 = world.timestamps[0]
    base = [step for step in build_default_scenario(t0).steps if step.table == "asset_register"]
    for i in range(8, len(world.timestamps), 20):
        at = world.timestamps[i]
        base.append(
            ScenarioStep(
                table="operation_log",
                values={
                    "log_id": f"OL-{i:05d}",
                    "asset_tag": "P-101",
                    "running": int(bool(labels[i])),
                    "shift": "shift1",
                    "logged_at": _iso(at),
                    "recorded_at": _iso(at + timedelta(minutes=1)),
                },
            )
        )
    at = world.timestamps[WRONG_AT]
    assert not labels[WRONG_AT], "台本の前提: 物理はここで停止している"
    wrong = ScenarioStep(
        table="operation_log",
        values={
            "log_id": "OL-WRONG",
            "asset_tag": "P-101",
            "running": 1,
            "shift": "shift1",
            "logged_at": _iso(at),
            "recorded_at": _iso(at + timedelta(minutes=1)),
        },
        note="MES は稼働中と言うが、物理は停止している（状態不一致の材料）",
    )
    base.append(wrong)
    return Scenario("g8-mes", tuple(base))


def _apply(db: Path, scenario: Scenario) -> None:
    for table in TABLES:
        with MockBusinessSystem(table=table, db_path=db) as mock:
            mock.apply(scenario)


class TestG8:
    @pytest.mark.usefixtures("topics")
    async def test_six_workers_run_on_the_real_services_from_outside_inputs(
        self,
        settings: Settings,
        site: str,
        tmp_path: Path,
        graph_store: HttpGraphStore,
        connection: psycopg.AsyncConnection[tuple[Any, ...]],
    ) -> None:
        world = SynthWorld(
            config=SynthConfig(
                start=now().replace(microsecond=0) - timedelta(hours=9),
                duration=timedelta(hours=8),
                cycles=8,
            ),
            seed=31,
        )
        pump = site_iri(site, "asset", "P-101")
        version = f"1.0.{time.time_ns()}"
        wm_version = f"{MODEL}/{version}"
        probe_id = f"https://sbp.example/probe/Running/{version}"
        db = tmp_path / "business.sqlite"
        run = settings.model_copy(
            update={
                "site_id": site,
                "integration": IntegrationSettings(mock_db_path=str(db), poll_period_s=1e-6),
                "bridge": BridgeSettings(
                    wm_version=wm_version,
                    probe_ids=(probe_id,),
                    weak_label_min_half_width_s=60.0,
                ),
                "reasoning": ReasoningSettings(reconcile_period_s=1e-6, bp_period_s=1e-6),
            }
        )

        # ---- 外界 1・2: 物理の観測窓（エッジのスプール）と、業務システムの台本 ----
        spool = tmp_path / "spool"
        spool.mkdir()
        (spool / "edge.jsonl").write_text(
            json.dumps(
                {
                    "entity": pump,
                    "channels": list(world.channels),
                    "timestamps": [at.astimezone(UTC).isoformat() for at in world.timestamps],
                    "values": world.observations.tolist(),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        _apply(db, _business_scenario(world))

        # ---- 推定パスの先頭: 学習 → 置き場 → エンコード → 潜在索引 ----
        store = S3ArtifactStore.from_settings(run)
        train_from_spool(run, spool, store=store, domain="d", delays=8, rank=None, seed=31)
        report = await encode_once(run, spool, store=store, connection=connection)
        assert report.rows_written > 2000

        # ---- 記録パス: 業務システム → 台帳・記録・主張イベント ----
        ingest, ingest_producer = await build_ingest_worker(run, site_id=site)
        try:
            ingested = 0
            while (n := await ingest.step()) > 0:
                ingested += n
            assert ingested > 0, "ingest-worker"
        finally:
            await ingest_producer.stop()

        # ---- 接地パスの学習: 弱ラベル（主張）と潜在 → プローブ束 ----
        assert await run_training(run, RUNNING, probe_id, 31, 3.0) == 0

        # ---- bridge-worker: 潜在 → プローブ → 事象昇格 ----
        bridge, bridge_producer = await build_bridge_worker(
            run,
            site_id=site,
            probe_ids=[probe_id],
            wm_version=wm_version,
            entities=(pump,),
        )
        try:
            promoted = 0
            idle = 0
            for _ in range(600):  # 潜在を読み切るまで（0 が続いたら終わり）
                n = await bridge.step()
                promoted += n
                idle = 0 if n else idle + 1
                if idle >= 5:
                    break
            assert promoted > 0, "bridge-worker"
        finally:
            await bridge_producer.stop()

        # ---- belief-worker ----
        # 履歴の再生では、流したイベントが増えるほど信念は 1.0 に飽和し、観測の効用が 0 になる
        # （確信している対象を観測し直す価値は無い。スケジューラの正しい挙動）。信念更新は
        # 1 ステップ 1 件にして、最初の 1 件を反映した（まだ迷っている）信念でスケジューラを動かす
        belief_run = run.model_copy(
            update={"reasoning": run.reasoning.model_copy(update={"batch_size": 1})}
        )
        belief, belief_consumer = await build_belief_worker(belief_run, site_id=site)
        try:
            assert await _until(lambda: belief.applied_total >= 1, belief.step), "belief-worker"
        finally:
            await belief_consumer.stop()
        assert AssertionStore(graph_store, site_id=site).query(subject=pump, predicate=RUNNING)

        # ---- reconcile-worker ----
        reconcile, reconcile_consumer, reconcile_producer = await build_reconcile_worker(
            run, site_id=site
        )
        raised = 0
        try:

            async def reconcile_step() -> None:
                nonlocal raised
                raised += await reconcile.step()

            assert await _until(lambda: raised > 0, reconcile_step), "reconcile-worker"
            await asyncio.sleep(0.5)
            await reconcile_step()
        finally:
            await reconcile_consumer.stop()
            await reconcile_producer.stop()

        # ---- 外界 3: 人の操作（判定不能）→ learning-worker → 観測要求 ----
        discrepancies = await DiscrepancyStore(connection, site=site).queue(limit=5)
        assert discrepancies, "不一致の表に行が無い"
        queue = DecisionQueue(connection, site=site)
        handler = build_command_handler(queue.enqueue)
        handler(
            "resolve_discrepancy",
            {
                "decision_id": "g8-resolve-1",
                "discrepancy_id": str(discrepancies[0].discrepancy_id),
                "outcome": "undetermined",
                "note": "現場から判断できない",
                "resolved_by": OPERATOR,
            },
        )
        await asyncio.sleep(0.3)
        learning, _decisions = await build_learning_worker(run, site_id=site)
        try:
            assert await learning.step() >= 1, "learning-worker"
        finally:
            await learning.aclose()
        assert await ObservationQueue(connection, site=site).outstanding()

        # ---- scheduler-worker: 観測要求 → 観測クエリ ----
        scheduler, _queue = await build_scheduler_worker(run, site_id=site)
        assert await scheduler.step() > 0, "scheduler-worker"
