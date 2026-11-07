"""再接地の実行の端から端まで（M9-01、ADR-0031）。

運用のコードで通す: 学習済み WM → 潜在索引 → 主張イベント → プローブ v1・v2 の学習（置き場）→
v1 でライブ相当の信念を書く → ``run_reground``（dry-run → apply → 再実行）。外界の模擬は観測窓と
業務の記録の台本だけ。確かめること:

1. dry-run は何も書かない。
2. apply は旧文の ``tx_to`` を閉じ、新文を足す（消さない）。``as_of`` で旧信念が引ける。
3. 再実行しても同じ。
4. ``regrounding_duration`` が指標の表に入る。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np
import psycopg
import pytest
from scripts.reground import run_reground
from scripts.train_probe import (
    _read_claims,  # pyright: ignore[reportPrivateUsage]
    run_training,
)
from services.belief_worker import default_ingest
from services.bridge_worker import load_probe_set, load_rules
from tests.integration import test_train_probe_e2e as base

from sbp.core.time import Interval, now
from sbp.knowledge import AssertionStore, HttpGraphStore, LatentIndex, MetricStore, S3ArtifactStore
from sbp.reasoning.regrounding_run import replay_entity

if TYPE_CHECKING:
    from pathlib import Path

    from sbp.core.config import Settings

FLUENT = base.FLUENT
topic = base.topic  # 同じ Kafka の fixture を使う
_prepare = base._prepare  # pyright: ignore[reportPrivateUsage]

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


class TestRegroundEndToEnd:
    @pytest.mark.asyncio
    async def test_dry_run_apply_and_rerun(
        self,
        settings: Settings,
        topic: tuple[str, str],
        tmp_path: Path,
    ) -> None:
        site, topic_ = topic
        configured, probe_v1 = await _prepare(settings, site, topic_, tmp_path)
        probe_v2 = probe_v1.replace("/1.0.", "/1.1.")
        assert await run_training(configured, FLUENT, probe_v1, 5, 3.0) == 0
        assert await run_training(configured, FLUENT, probe_v2, 99, 3.0) == 0
        entity = f"https://sbp.example/site/{site}/asset/P-PROBE"

        # ---- ライブ相当: v1 で再生した信念を、1 時間前の取引時刻で書く ----
        store = AssertionStore(HttpGraphStore(configured), site_id=site)
        old_tx = now() - timedelta(hours=1)
        async with await psycopg.AsyncConnection.connect(
            configured.pg_dsn, autocommit=True
        ) as connection:
            rows = await LatentIndex(connection).since(
                entity=entity, wm_version=configured.bridge.wm_version, level="mid", limit=100_000
            )
        series = ([at for at, _v in rows], np.vstack([v for _a, v in rows]))
        artifacts = S3ArtifactStore.from_settings(configured)
        v1 = load_probe_set(artifacts, probe_ids=[probe_v1])
        claims = await _read_claims(configured, idle_s=3.0)
        outcomes = replay_entity(
            entity=entity,
            series=series,
            probes=v1,
            probe_ids=v1.probe_ids(),
            wm_version=v1.wm_version,
            rules=load_rules(),
            settings=configured,
            claims=claims,
            ingest=default_ingest(configured),
        )
        live = [o.assertion for o in outcomes if o.assertion is not None]
        assert live, "前提: ライブの信念がある"
        for assertion in live:
            store.assert_(assertion.model_copy(update={"tx_from": old_tx}))

        interval = Interval(rows[0][0], rows[-1][0] + timedelta(seconds=1))
        common = {
            "entities": [entity],
            "fluents": [FLUENT],
            "interval": interval,
            "probe_ids": [probe_v2],
            "from_version": probe_v1,
            "to_version": probe_v2,
            "reason": "Running プローブを v2 に昇格",
        }

        # ---- 1. dry-run は書かない ----
        before = store.query(subject=entity, predicate=FLUENT, include_closed=True)
        dry = await run_reground(configured, apply=False, **common)  # type: ignore[arg-type]
        assert not dry.applied
        assert dry.added, "v2 は v1 と確率が違う"
        assert store.query(subject=entity, predicate=FLUENT, include_closed=True) == before

        # ---- 2. apply は閉じて足す。消さない ----
        applied = await run_reground(configured, apply=True, **common)  # type: ignore[arg-type]
        assert applied.applied
        after_rows = store.query(subject=entity, predicate=FLUENT, include_closed=True)
        assert len(after_rows) >= len(before)
        closed_ids = {row.assertion_id for row in applied.closed}
        assert all(row.tx_to is not None for row in after_rows if row.assertion_id in closed_ids)
        stamp = applied.report.finished_at
        reconstructed = store.query(
            subject=entity, predicate=FLUENT, as_of=old_tx + timedelta(seconds=1)
        )
        assert reconstructed, "再接地の前の信念が as_of で引ける"
        assert all("regrounding" not in row.provenance for row in reconstructed)

        # ---- 3. 再実行しても同じ ----
        again = await run_reground(configured, apply=True, **common)  # type: ignore[arg-type]
        assert not again.added
        assert not again.closed
        assert stamp <= again.report.finished_at

        # ---- 4. 指標 ----
        async with await psycopg.AsyncConnection.connect(
            configured.pg_dsn, autocommit=True
        ) as connection:
            samples = await MetricStore(connection, site=site).latest()
        assert any(m.name.key == "regrounding_duration" for m in samples), "指標が表に入る"
