"""推定パスの先頭の端から端まで（スプール → 学習 → 置き場 → エンコード → 潜在索引）。

M8-24-5（ADR-0023）。現場のエッジが置く観測窓（JSONL）を**運用のコード**で通す:
``train_from_spool`` → ``encode_once`` → ``LatentIndex.since``。
テストが模擬するのは外界（観測窓）だけ。

確かめること:

1. スプールから学習した重みが置き場に置かれ、``encode_once`` が読み戻して潜在を索引に書く。
2. 書いた潜在が bridge-worker と同じ読み口（``since``）で時刻順に読める。
3. **2 回目は何も書かない**（ウォーターマークで冪等）。新しい窓が来たぶんだけ増える。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING

import psycopg
import pytest
from scripts.encode import encode_once
from scripts.train_koopman import train_from_spool

from sbp.core.config import BridgeSettings, Settings
from sbp.core.time import now
from sbp.knowledge import LatentIndex, MetricStore
from sbp.knowledge.artifacts import LocalArtifactStore
from sbp.testing import SynthConfig, SynthWorld

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

ASSET = "https://sbp.example/site/siteA/asset/P-ENC-1"
MODEL = "https://sbp.example/model/encode-e2e-koopman"
LEVEL = "mid"


def _append(path: Path, world: SynthWorld, start: int, stop: int) -> None:
    window = {
        "entity": ASSET,
        "channels": list(world.channels),
        "timestamps": [at.isoformat() for at in world.timestamps[start:stop]],
        "values": world.observations[start:stop].tolist(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(window) + "\n")


@pytest.mark.asyncio
async def test_spool_to_latent_index(settings: Settings, tmp_path: Path) -> None:
    world = SynthWorld(
        config=SynthConfig(
            start=now().replace(microsecond=0) - timedelta(hours=3),
            duration=timedelta(hours=3),
            cycles=3,
        ),
        seed=23,
    )
    version = f"1.0.{int(now().timestamp())}"  # 再実行しても版が衝突しない
    configured = settings.model_copy(
        update={"bridge": BridgeSettings(wm_version=f"{MODEL}/{version}")}
    )
    spool = tmp_path / "spool"
    spool.mkdir()
    store = LocalArtifactStore(tmp_path / "store")
    _append(spool / "a.jsonl", world, 0, 200)

    model, key = train_from_spool(
        configured, spool, store=store, domain="d", delays=8, rank=None, seed=23
    )
    assert key.endswith(f"{version}/koopman.npz")

    async with await psycopg.AsyncConnection.connect(
        settings.pg_dsn, autocommit=True
    ) as connection:
        first = await encode_once(configured, spool, store=store, connection=connection)
        assert first.rows_written == 200 - 7, "時間遅れ 8 段ぶんは先頭から作れない"
        assert first.rejected == 0

        rows = await LatentIndex(connection).since(
            entity=ASSET, wm_version=model.meta.version_iri, level=LEVEL
        )
        assert len(rows) == first.rows_written
        stamps = [at for at, _vec in rows]
        assert stamps == sorted(stamps)
        assert rows[0][1].shape == (model.latent_dim,)

        latest = await MetricStore(connection, site=configured.site_id).latest()
        measured = {
            m.name.key for m in latest if m.labels.get("wm_version") == model.meta.version_iri
        }
        assert {"inference_latency", "latent_shift", "pred_error"} <= measured, "M8-15"

        again = await encode_once(configured, spool, store=store, connection=connection)
        assert again.rows_written == 0, "処理済みの窓を書き直さない"
        assert again.skipped_processed >= 1

        _append(spool / "a.jsonl", world, 150, 260)
        grown = await encode_once(configured, spool, store=store, connection=connection)
        assert grown.rows_written == 60, "ウォーターマークより後の時刻だけが増える"
