"""What-if ジョブの端から端まで（M10-03、ADR-0036）。

運用のコードで通す: 介入の列つきのスプール → 学習 → 潜在 → プローブ → ``whatif_once``
（最新の潜在を起点に、介入ありと基準を並べ、``hypothetical`` の文と索引を書く）。確かめること:

1. 介入付きの WM で、シナリオが索引に書かれ、結果の文は**主信念に混ざらない**
   （シナリオ用グラフにだけある）。
2. 同じ問いは 2 度書かない（索引も文も）。
3. **介入付きでない WM は拒否する**（理由つき。何も書かない）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest
from scripts._wm import split_wm_version
from scripts.train_probe import run_training
from scripts.whatif import whatif_once
from services.bridge_worker import load_probe_set
from tests.integration import test_train_probe_e2e as base

from sbp.core.modality import Modality
from sbp.knowledge import AssertionStore, HttpGraphStore, S3ArtifactStore, ScenarioStore
from sbp.knowledge.artifacts import LocalArtifactStore
from sbp.reasoning.whatif_run import WhatIfRefusedError
from sbp.worldmodel.serialization import load_model

if TYPE_CHECKING:
    from pathlib import Path

    from sbp.core.config import Settings

FLUENT = base.FLUENT
ACTION = "https://sbp.example/ent#StopCmd"
topic = base.topic
_prepare = base._prepare  # pyright: ignore[reportPrivateUsage]

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def _setup(settings: Settings, site: str, topic_: str, tmp_path: Path, *, actions: bool):  # type: ignore[no-untyped-def]
    configured, probe_id = await _prepare(settings, site, topic_, tmp_path, with_actions=actions)
    assert await run_training(configured, FLUENT, probe_id, 5, 3.0) == 0
    configured = configured.model_copy(
        update={
            "reasoning": configured.reasoning.model_copy(
                update={"forecast_horizon_steps": 6, "forecast_max_origin_age_s": 86_400.0}
            )
        }
    )
    model_id, version = split_wm_version(configured.bridge.wm_version)
    model = load_model(LocalArtifactStore(tmp_path / "wm"), model_id=model_id, version=version)
    probes = load_probe_set(S3ArtifactStore.from_settings(configured), probe_ids=[probe_id])
    entity = f"https://sbp.example/site/{site}/asset/P-PROBE"
    store = AssertionStore(HttpGraphStore(configured), site_id=site)
    return configured, model, probes, entity, store, probe_id


class TestWhatIfEndToEnd:
    @pytest.mark.asyncio
    async def test_an_intervention_whatif_is_written_and_isolated(
        self, settings: Settings, topic: tuple[str, str], tmp_path: Path
    ) -> None:
        site, topic_ = topic
        configured, model, probes, entity, store, probe_id = await _setup(
            settings, site, topic_, tmp_path, actions=True
        )
        assert model.action_dim == 2
        async with await psycopg.AsyncConnection.connect(
            configured.pg_dsn, autocommit=True
        ) as connection:
            args = {
                "entity": entity,
                "fluent": FLUENT,
                "action_type": ACTION,
                "channel": 0,
                "magnitude": 3.0,
                "horizon": 6,
                "model": model,
                "probes": probes,
                "seed": 3,
                "probe_ids": [probe_id],
                "connection": connection,
                "store": store,
            }
            result = await whatif_once(configured, **args)  # type: ignore[arg-type]
            assert result is not None
            graph = result.scenario.graph_name
            in_scenario = store.query(
                subject=entity, predicate=FLUENT, modality=Modality.HYPOTHETICAL, scenario=graph
            )
            assert len(in_scenario) == 6
            assert store.query(subject=entity, predicate=FLUENT) == [], "主信念に混ざらない"

            records = await ScenarioStore(connection, site=site).recent(entity=entity)
            assert len(records) == 1
            assert records[0].payload["graph"] == graph
            assert len(records[0].payload["delta"]) == 6

            await whatif_once(configured, **args)  # type: ignore[arg-type]
            assert len(await ScenarioStore(connection, site=site).recent(entity=entity)) == 1
            again = store.query(
                subject=entity, predicate=FLUENT, modality=Modality.HYPOTHETICAL, scenario=graph
            )
            assert len(again) == 6, "同じ問いは文も二重に書かない"

    @pytest.mark.asyncio
    async def test_a_model_without_interventions_is_refused_and_writes_nothing(
        self, settings: Settings, topic: tuple[str, str], tmp_path: Path
    ) -> None:
        site, topic_ = topic
        configured, model, probes, entity, store, probe_id = await _setup(
            settings, site, topic_, tmp_path, actions=False
        )
        assert model.action_dim is None
        async with await psycopg.AsyncConnection.connect(
            configured.pg_dsn, autocommit=True
        ) as connection:
            with pytest.raises(WhatIfRefusedError, match="fitted without interventions"):
                await whatif_once(
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
            assert await ScenarioStore(connection, site=site).recent(entity=entity) == []
