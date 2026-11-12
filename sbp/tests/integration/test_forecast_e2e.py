"""予測ジョブの端から端まで（M9-02、ADR-0032）。

運用のコードで通す: 学習済み WM → 潜在索引 → 主張イベント → プローブの学習（置き場）→
``forecast_once``（最新の潜在を起点に予測 → 予測の表と ``predicted`` のアサーション → 採点）。
外界の模擬は観測窓と業務の記録の台本だけ。確かめること:

1. 予測が表に書かれ、``predicted`` のアサーションが主信念の問い合わせに混ざらない。
2. 同じ起点は 2 度書かない。
3. 地平が過ぎた予測は、信念と突き合わせて採点され、``forecast_crps`` / ``forecast_coverage`` が
   指標の表に入り、採点済みになる（二重に採点しない）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import psycopg
import pytest
from scripts._wm import split_wm_version
from scripts.forecast import forecast_once
from scripts.train_probe import run_training
from services.bridge_worker import load_probe_set
from tests.integration import test_train_probe_e2e as base

from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.knowledge import (
    AssertionStore,
    ForecastStore,
    HttpGraphStore,
    MetricStore,
    S3ArtifactStore,
)
from sbp.knowledge.artifacts import LocalArtifactStore
from sbp.worldmodel.serialization import load_model

if TYPE_CHECKING:
    from pathlib import Path

    from sbp.core.config import Settings

FLUENT = base.FLUENT
topic = base.topic
_prepare = base._prepare  # pyright: ignore[reportPrivateUsage]

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


class TestForecastEndToEnd:
    @pytest.mark.asyncio
    async def test_forecast_write_and_score(
        self, settings: Settings, topic: tuple[str, str], tmp_path: Path
    ) -> None:
        site, topic_ = topic
        configured, probe_id = await _prepare(settings, site, topic_, tmp_path)
        assert await run_training(configured, FLUENT, probe_id, 5, 3.0) == 0
        configured = configured.model_copy(
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
        entity = f"https://sbp.example/site/{site}/asset/P-PROBE"
        artifacts = S3ArtifactStore.from_settings(configured)
        model_id, version = split_wm_version(configured.bridge.wm_version)
        model = load_model(LocalArtifactStore(tmp_path / "wm"), model_id=model_id, version=version)
        probes = load_probe_set(artifacts, probe_ids=[probe_id])
        store = AssertionStore(HttpGraphStore(configured), site_id=site)
        # 信念: 採点の相手（地平より前から「成立」）
        store.assert_(
            Assertion(
                subject=entity,
                predicate=FLUENT,
                object=True,
                valid_from=now() - timedelta(days=1),
                tx_from=now() - timedelta(days=1),
                modality=Modality.INFERRED,
                belief=BooleanBelief(p=0.9),
                provenance="urn:sbp:prov:belief-worker/x",
                ontology_version=vocab.ONTOLOGY_VERSION,
            )
        )

        async with await psycopg.AsyncConnection.connect(
            configured.pg_dsn, autocommit=True
        ) as connection:
            written, scored = await forecast_once(
                configured,
                entities=[entity],
                fluents=[FLUENT],
                model=model,
                probes=probes,
                seed=3,
                connection=connection,
                store=store,
            )
            assert written == 1
            predicted = store.query(subject=entity, predicate=FLUENT, modality=Modality.PREDICTED)
            assert predicted, "predicted のアサーションが書かれた"
            assert all(row.modality is Modality.PREDICTED for row in predicted)
            inferred = store.query(subject=entity, predicate=FLUENT, modality=Modality.INFERRED)
            assert all(row.confidence == pytest.approx(0.9) for row in inferred)

            # 地平は過去（潜在は 9 時間前の窓）。1 周期目で書いて、採点は同じ周期で終わる
            assert scored == 1
            again_written, again_scored = await forecast_once(
                configured,
                entities=[entity],
                fluents=[FLUENT],
                model=model,
                probes=probes,
                seed=3,
                connection=connection,
                store=store,
            )
            assert again_written == 0, "同じ起点は書かない"
            assert again_scored == 0, "採点済みを二重に採点しない"

            keys = {m.name.key for m in await MetricStore(connection, site=site).latest()}
            assert {"forecast_crps", "forecast_coverage"} <= keys
            records = await ForecastStore(connection, site=site).recent(entity=entity)
            assert len(records) == 1
            assert records[0].scored_at is not None
