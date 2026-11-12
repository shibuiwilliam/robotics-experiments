"""ゲート G9: 運用の入口（M9-04、ADR-0030）。

外界（観測窓と業務の記録の台本）だけを置き、運用のコードで通す:
学習済み WM → 潜在 → プローブ v1・v2 → 信念（v1 で再生）→
**再接地の dry-run / apply**（M9-01）→ **予測と採点**（M9-02）→
**実物の API から予測を読む**（M9-03）。

確かめること:

1. 再接地・予測・採点が、運用の入口（``run_reground`` / ``forecast_once``）だけで通る。
2. 指標 ``regrounding_duration`` / ``forecast_crps`` / ``forecast_coverage`` が表に入る。
3. API が ABAC を通して予測を返し、要求者の無い呼び出しは拒否する。
4. コマンドは要求者に結びつく（H-25）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient
from scripts._wm import split_wm_version
from scripts.forecast import forecast_once
from scripts.reground import run_reground
from scripts.train_probe import (
    _read_claims,  # pyright: ignore[reportPrivateUsage]
    run_training,
)
from services.belief_worker import default_ingest
from services.bridge_worker import load_probe_set, load_rules
from tests.integration import test_train_probe_e2e as base

from sbp.api.factory import build_app
from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.modality import Modality
from sbp.core.time import Interval, now
from sbp.knowledge import (
    AssertionStore,
    HttpGraphStore,
    LatentIndex,
    MetricStore,
    S3ArtifactStore,
)
from sbp.knowledge.artifacts import LocalArtifactStore
from sbp.reasoning.regrounding_run import replay_entity
from sbp.worldmodel.serialization import load_model

if TYPE_CHECKING:
    from pathlib import Path

    from sbp.core.config import Settings

FLUENT = base.FLUENT
topic = base.topic
_prepare = base._prepare  # pyright: ignore[reportPrivateUsage]

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

OPERATOR = "https://sbp.example/id/person/g9"


class TestG9:
    @pytest.mark.asyncio
    async def test_regrounding_forecasting_and_the_api_through_operating_entrances(
        self, settings: Settings, topic: tuple[str, str], tmp_path: Path
    ) -> None:
        site, topic_ = topic
        configured, probe_v1 = await _prepare(settings, site, topic_, tmp_path)
        probe_v2 = probe_v1.replace("/1.0.", "/1.1.")
        assert await run_training(configured, FLUENT, probe_v1, 5, 3.0) == 0
        assert await run_training(configured, FLUENT, probe_v2, 99, 3.0) == 0
        entity = f"https://sbp.example/site/{site}/asset/P-PROBE"
        store = AssertionStore(HttpGraphStore(configured), site_id=site)
        artifacts = S3ArtifactStore.from_settings(configured)

        # ---- 信念（ライブ相当）: v1 で再生して 1 時間前の取引時刻で書く ----
        async with await psycopg.AsyncConnection.connect(
            configured.pg_dsn, autocommit=True
        ) as connection:
            rows = await LatentIndex(connection).since(
                entity=entity, wm_version=configured.bridge.wm_version, level="mid", limit=100_000
            )
        v1 = load_probe_set(artifacts, probe_ids=[probe_v1])
        claims = await _read_claims(configured, idle_s=3.0)
        outcomes = replay_entity(
            entity=entity,
            series=([at for at, _v in rows], np.vstack([v for _a, v in rows])),
            probes=v1,
            probe_ids=v1.probe_ids(),
            wm_version=v1.wm_version,
            rules=load_rules(),
            settings=configured,
            claims=claims,
            ingest=default_ingest(configured),
        )
        for outcome in outcomes:
            assert outcome.assertion is not None
            store.assert_(
                outcome.assertion.model_copy(update={"tx_from": now() - timedelta(hours=1)})
            )

        # ---- 1. 再接地（dry-run → apply）----
        common = {
            "entities": [entity],
            "fluents": [FLUENT],
            "interval": Interval(rows[0][0], rows[-1][0] + timedelta(seconds=1)),
            "probe_ids": [probe_v2],
            "from_version": probe_v1,
            "to_version": probe_v2,
            "reason": "G9: Running プローブを v2 に昇格",
        }
        dry = await run_reground(configured, apply=False, **common)  # type: ignore[arg-type]
        assert dry.added
        applied = await run_reground(configured, apply=True, **common)  # type: ignore[arg-type]
        assert applied.applied

        # ---- 2. 予測と採点（再接地後の信念が相手）----
        forecasting = configured.model_copy(
            update={
                "reasoning": configured.reasoning.model_copy(
                    update={
                        "forecast_horizon_steps": 6,
                        "forecast_stride": 3,
                        "forecast_grace_s": 0.0,
                        "forecast_max_origin_age_s": 86_400.0,
                    }
                )
            }
        )
        model_id, version = split_wm_version(forecasting.bridge.wm_version)
        model = load_model(LocalArtifactStore(tmp_path / "wm"), model_id=model_id, version=version)
        probes = load_probe_set(artifacts, probe_ids=[probe_v2])
        # 地平の時刻に有効な信念を 1 件足す（採点の相手。合成の窓の終端より後も開いている）
        store.assert_(
            Assertion(
                subject=entity,
                predicate=FLUENT,
                object=True,
                valid_from=now() - timedelta(days=1),
                tx_from=now() - timedelta(days=1),
                modality=Modality.INFERRED,
                belief=BooleanBelief(p=0.9),
                provenance="urn:sbp:prov:belief-worker/g9",
                ontology_version=vocab.ONTOLOGY_VERSION,
            )
        )
        async with await psycopg.AsyncConnection.connect(
            forecasting.pg_dsn, autocommit=True
        ) as connection:
            written, scored = await forecast_once(
                forecasting,
                entities=[entity],
                fluents=[FLUENT],
                model=model,
                probes=probes,
                seed=3,
                connection=connection,
                store=store,
            )
            assert written == 1
            assert scored == 1
            keys = {m.name.key for m in await MetricStore(connection, site=site).latest()}
        assert {"regrounding_duration", "forecast_crps", "forecast_coverage"} <= keys

        # ---- 3. 実物の API から予測を読む（ABAC）。コマンドは要求者に結びつく（H-25）----
        dev = forecasting.model_copy(update={"env": "dev"})
        with TestClient(build_app(dev)) as client:
            query = {
                "query": "{ forecasts { entity fluent wmVersion scored "
                "points { leadTimeS mean sampleCount } } }"
            }
            denied = client.post("/graphql", json=query).json()
            assert denied.get("errors"), "要求者の無い呼び出しは拒否"
            body = client.post("/graphql", json=query, headers={"X-SBP-Principal": OPERATOR}).json()
            assert "errors" not in body, body
            row = body["data"]["forecasts"][0]
            assert row["entity"] == entity
            assert row["scored"] is True
            assert row["fluent"] == FLUENT
            assert row["wmVersion"] == forecasting.bridge.wm_version
            assert row["points"]
            assert all(
                p["sampleCount"] == forecasting.reasoning.forecast_samples for p in row["points"]
            )
            assert all(0.0 <= p["mean"] <= 1.0 for p in row["points"])

            spoof = client.post(
                "/commands/answer_observation",
                json={"answered_by": "https://sbp.example/id/person/someone-else"},
                headers={"X-SBP-Principal": OPERATOR},
            )
            assert spoof.status_code == 403
            anonymous = client.post("/commands/answer_observation", json={})
            assert anonymous.status_code == 401
