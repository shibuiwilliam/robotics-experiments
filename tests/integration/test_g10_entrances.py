"""ゲート G10: 運用の入口 その 2（M10-05、ADR-0035・0036）。

外界（業務システムの壊れた行、観測窓と介入の列、人の操作）だけを置き、運用のコードで通す:

A. **隔離**: 壊れた行を含む業務システム → ``IngestWorker`` → 隔離キュー（実 Postgres）→
   **実物の API** ``quarantine`` → コマンド ``review_quarantine``（要求者に結びつく）→
   ``LearningWorker`` → 確認済み。
B. **What-if**: 介入の列つきのスプール → 学習 → ``whatif_once`` → 索引と ``hypothetical`` の文 →
   実物の API ``scenarios``（既定の要求者には見えない）。
   介入付きでない WM は拒否する。

確かめること: 壊れた入力が捨てられず人の確認に届く／確認は要求者の名で残る／
``hypothetical`` が主信念にも既定の要求者にも出ない。
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
from fastapi.testclient import TestClient
from scripts._wm import split_wm_version
from scripts.train_probe import run_training
from scripts.whatif import whatif_once
from services.bridge_worker import load_probe_set
from services.ingest_worker import IngestWorker
from services.learning_worker import LearningWorker, quarantine_reviewer
from tests.integration import test_train_probe_e2e as base

from sbp.api.factory import build_app
from sbp.core.config import IntegrationSettings
from sbp.integration import MappingRegistry, MasterDataSync, ReliabilityRegistry
from sbp.integration.mock import MockBusinessSystem
from sbp.integration.mock.business_system import build_default_scenario
from sbp.knowledge import (
    AssertionStore,
    DecisionQueue,
    HttpGraphStore,
    QuarantineStore,
    S3ArtifactStore,
    ScenarioStore,
)
from sbp.knowledge.artifacts import LocalArtifactStore
from sbp.worldmodel.serialization import load_model

if TYPE_CHECKING:
    from sbp.core.config import Settings
    from sbp.core.events import EventEnvelope

FLUENT = base.FLUENT
topic = base.topic
_prepare = base._prepare  # pyright: ignore[reportPrivateUsage]
T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
TABLES = ("asset_register", "operation_log", "work_order", "spare_part_stock", "maintenance_plan")
OPERATOR = "https://sbp.example/id/person/g10"
ACTION = "https://sbp.example/ent#StopCmd"
ROOT = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


class TestG10Quarantine:
    @pytest.mark.asyncio
    async def test_broken_rows_reach_a_human_and_the_review_is_recorded(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        site = f"it-g10q-{tmp_path.name[-6:]}"
        configured = settings.model_copy(update={"site_id": site, "env": "dev"})
        db = tmp_path / "mock.sqlite"
        connectors = {t: MockBusinessSystem(table=t, db_path=db) for t in TABLES}
        for connector in connectors.values():
            connector.apply(build_default_scenario(T0))
        # 外界: 壊れた行（型がオントロジーに無い台帳の行・時刻が読めない運転記録）
        with closing(sqlite3.connect(db)) as raw, raw:
            raw.execute(
                "INSERT INTO asset_register (asset_tag, asset_type, part_of, recorded_at, "
                "updated_at) VALUES ('X-1', 'Gizmo', NULL, ?, ?)",
                (T0.isoformat(), T0.isoformat()),
            )
            raw.execute(
                "INSERT INTO operation_log (log_id, asset_tag, running, shift, logged_at, "
                "recorded_at, updated_at) VALUES ('OL-BAD', 'P-101', 1, 's', 'x', 'x', ?)",
                (T0.isoformat(),),
            )
        graph = HttpGraphStore(configured)
        sent: list[EventEnvelope] = []

        async def sink(_topic: str, event: EventEnvelope) -> None:
            sent.append(event)

        async with await psycopg.AsyncConnection.connect(
            configured.pg_dsn, autocommit=True
        ) as connection:
            store = QuarantineStore(connection, site=site)

            async def quarantine(rejection) -> bool:  # type: ignore[no-untyped-def]
                return await store.add(
                    source=rejection.source,
                    table=rejection.table,
                    subject=rejection.subject,
                    reason=rejection.reason,
                    payload=rejection.payload,
                )

            ingest = IngestWorker(
                configured.model_copy(
                    update={"integration": IntegrationSettings(poll_period_s=1e-6)}
                ),
                connectors=connectors,
                mappings=MappingRegistry.from_directory(ROOT / "mappings"),
                reliability=ReliabilityRegistry.from_directory(ROOT / "reliability"),
                sink=sink,
                master=MasterDataSync(AssertionStore(graph, site_id=site), graph, site_id=site),
                quarantine=quarantine,
            )
            await ingest.step()
            assert sent, "壊れた行の隣の健全な行は送られる"
            pending = await store.recent()
            assert {r.subject for r in pending} == {"X-1", "OL-BAD"}
            await ingest.step()
            assert len(await store.recent()) == 2, "毎周期落ち続けても 1 行ずつ"

            # ---- 実物の API から読み、確認コマンドを出す（要求者に結びつく）----
            with TestClient(build_app(configured)) as client:
                query = {"query": "{ quarantine { eventId subject reason payload } }"}
                assert client.post("/graphql", json=query).json().get("errors"), "要求者なしは拒否"
                headers = {"X-SBP-Principal": OPERATOR}
                body = client.post("/graphql", json=query, headers=headers).json()
                assert "errors" not in body, body
                rows = {r["subject"]: r for r in body["data"]["quarantine"]}
                assert set(rows) == {"X-1", "OL-BAD"}
                assert "Gizmo" in rows["X-1"]["payload"], "生の入力が人に見える"

                spoof = client.post(
                    "/commands/review_quarantine",
                    json={"reviewed_by": "https://sbp.example/id/person/other"},
                    headers=headers,
                )
                assert spoof.status_code == 403
                ok = client.post(
                    "/commands/review_quarantine",
                    json={
                        "decision_id": "g10-r1",
                        "quarantine_id": rows["X-1"]["eventId"],
                        "resolution": "業務側で型を修正する",
                    },
                    headers=headers,
                )
                assert ok.status_code == 200, ok.text

                # 受け付けは非同期（API は積むだけ）。learning-worker が適用する
                queue = DecisionQueue(connection, site=site)
                for _ in range(50):
                    if await queue.pending():
                        break
                    await asyncio.sleep(0.1)
                worker = LearningWorker(
                    configured,
                    decisions=queue,
                    quarantine=quarantine_reviewer(connection, site),
                )
                assert await worker.step() == 1

                after = client.post("/graphql", json=query, headers=headers).json()
                assert [r["subject"] for r in after["data"]["quarantine"]] == ["OL-BAD"]
            [reviewed] = [
                r for r in await store.recent(only_unreviewed=False) if r.subject == "X-1"
            ]
            assert reviewed.resolution == "業務側で型を修正する"
            decision = (await queue.history(subject=str(reviewed.event_id)))[0]
            assert decision.decided_by == OPERATOR, "確認は要求者の名で残る"
            assert decision.state == "applied"
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM quarantine WHERE site = %s", (site,))
                await cursor.execute("DELETE FROM learning_decisions WHERE site = %s", (site,))


class TestG10WhatIf:
    @pytest.mark.asyncio
    async def test_a_whatif_is_stored_hidden_from_the_default_viewer_and_refused_without_actions(
        self, settings: Settings, topic: tuple[str, str], tmp_path: Path
    ) -> None:
        site, topic_ = topic
        configured, probe_id = await _prepare(settings, site, topic_, tmp_path, with_actions=True)
        assert await run_training(configured, FLUENT, probe_id, 5, 3.0) == 0
        configured = configured.model_copy(
            update={
                "env": "dev",
                "reasoning": configured.reasoning.model_copy(
                    update={"forecast_horizon_steps": 6, "forecast_max_origin_age_s": 86_400.0}
                ),
            }
        )
        entity = f"https://sbp.example/site/{site}/asset/P-PROBE"
        model_id, version = split_wm_version(configured.bridge.wm_version)
        model = load_model(LocalArtifactStore(tmp_path / "wm"), model_id=model_id, version=version)
        probes = load_probe_set(S3ArtifactStore.from_settings(configured), probe_ids=[probe_id])
        store = AssertionStore(HttpGraphStore(configured), site_id=site)
        assert model.action_dim == 2
        async with await psycopg.AsyncConnection.connect(
            configured.pg_dsn, autocommit=True
        ) as connection:
            result = await whatif_once(
                configured,
                entity=entity,
                fluent=FLUENT,
                action_type=ACTION,
                channel=0,
                magnitude=3.0,
                horizon=6,
                model=model,
                probes=probes,
                seed=3,
                probe_ids=[probe_id],
                connection=connection,
                store=store,
            )
            assert result is not None
            stored = await ScenarioStore(connection, site=site).recent(entity=entity)
            assert len(stored) == 1

        # 実物の API: 既定の要求者（hypothetical を含まない）には見えない
        with TestClient(build_app(configured)) as client:
            query = {"query": "{ scenarios { scenarioId } }"}
            assert client.post("/graphql", json=query).json().get("errors"), "要求者なしは拒否"
            body = client.post("/graphql", json=query, headers={"X-SBP-Principal": OPERATOR}).json()
            assert "errors" not in body, body
            assert body["data"]["scenarios"] == [], "hypothetical は既定の要求者に見せない"
        # 主信念にも混ざらない
        assert store.query(subject=entity, predicate=FLUENT) == []
