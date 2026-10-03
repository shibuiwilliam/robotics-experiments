"""接地ワーカーの端から端まで（潜在索引 → プローブ → 事象昇格 → イベント）。

推定パス（設計書 §2.5）の後半を**実物の潜在索引**で通す。これまで単体テストは読み出しを
関数で差し替えていたので、``LatentIndex.since`` の SQL と pgvector の読み戻しは一度も
実行されていなかった。

ここで確かめること:

1. 実モデル（koopman）の潜在を書いて ``since`` で時刻順に読み戻せる。
2. **``since`` が右開である** — ウォーターマークを「最後に処理した時刻」として持てる。
3. 読み戻した潜在から線形プローブ → 事象昇格が通り、``inferred`` のイベントが出る。
4. 2 周目は新しい潜在だけを読む（同じ変化点を繰り返さない）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import psycopg
import pytest
from services.bridge_worker import BridgeWorker

from sbp.bridge.lifting import load_event_calculus
from sbp.core.config import Settings
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.knowledge import LatentIndex
from sbp.testing.synth import Fluent, SynthConfig, SynthWorld
from sbp.worldmodel.families.koopman import KoopmanConfig, KoopmanModel

if TYPE_CHECKING:
    from datetime import datetime

    from numpy.typing import NDArray

    from sbp.core.events import EventEnvelope

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

PUMP = "https://sbp.example/site/siteA/asset/P-101"
LEVEL = "mid"

_RULES = load_event_calculus("""
initiates(start_cmd, running).
terminates(stop_cmd, running).
""")


@pytest.fixture
def world() -> SynthWorld:
    """合成世界（起動停止を 4 回繰り返す = 変化点が複数）。"""
    return SynthWorld(
        config=SynthConfig(
            start=now().replace(microsecond=0) - timedelta(hours=4),
            duration=timedelta(hours=4),
            cycles=4,
        ),
        seed=11,
    )


@pytest.fixture
def koopman(world: SynthWorld) -> KoopmanModel:
    """実モデル（合成潜在ではなく koopman の潜在を使う）。

    版 IRI をこのテスト専用にする——G4 の潜在索引テストと同じ版を使うと、
    登録済みの次元が衝突する可能性がある。
    """
    model = KoopmanModel.untrained(
        model_id="https://sbp.example/model/bridge-e2e-koopman",
        version="1.0.0",
        domain="https://sbp.example/ent#Pump",
        channels=world.channels,
        config=KoopmanConfig(delays=8),  # 3 チャンネル × 8 = 24 次元（latent_index_d24 に合わせる）
    )
    return model.fit(world.observations, seed=11)


class _Sink:
    def __init__(self) -> None:
        self.sent: list[EventEnvelope] = []

    def __call__(self, event: EventEnvelope) -> None:
        self.sent.append(event)


def _probe_runner(world: SynthWorld, koopman: KoopmanModel) -> tuple[object, int]:
    """真値から線形プローブを学習し、較正済み確率を返す runner を作る。

    真値を使うのは ``sbp.testing`` 由来の学習だから（不変条件 10 の範囲内——テストコード内
    でのみ真値を参照する）。**プローブの精度を測っているのではなく、配線が通ることを見ている。**
    """
    from sbp.bridge.calibration import TemperatureScaler  # noqa: PLC0415
    from sbp.bridge.probes import LinearProbe  # noqa: PLC0415

    latents = koopman.encode_series(world.observations)
    labels = world.oracle.labels_for(Fluent.RUNNING)[-latents.shape[0] :]
    probe = LinearProbe(dim=latents.shape[1], seed=11)
    probe.fit(latents, labels, epochs=400)
    # 不変条件 7: 較正器を付けてから predict する（較正済みの値だけを信念に入れる）
    probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents), labels))

    def run(window: NDArray[np.float64]) -> dict[str, NDArray[np.float64]]:
        return {"Running": probe.predict(window).probabilities}

    return run, latents.shape[1]


async def _write_latents(
    connection: psycopg.AsyncConnection[tuple[Any, ...]],
    *,
    koopman: KoopmanModel,
    world: SynthWorld,
    count: int,
) -> list[datetime]:
    """潜在を索引に書く。"""
    latents = koopman.encode_series(world.observations)[:count]
    stamps = list(world.timestamps[-latents.shape[0] :])[:count]
    index = LatentIndex(connection)
    await index.register(wm_version=koopman.meta.version_iri, dim=koopman.latent_dim)
    await index.write(
        entity=PUMP,
        wm_version=koopman.meta.version_iri,
        level=LEVEL,
        timestamps=stamps,
        vectors=latents,
    )
    return stamps


class TestLatentReadback:
    """``LatentIndex.since`` の実挙動。"""

    @pytest.mark.asyncio
    async def test_latents_come_back_in_time_order(
        self, settings: Settings, koopman: KoopmanModel, world: SynthWorld
    ) -> None:
        """書いた潜在が時刻順に読み戻せる（pgvector の往復も含む）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            stamps = await _write_latents(connection, koopman=koopman, world=world, count=50)
            index = LatentIndex(connection)
            rows = await index.since(entity=PUMP, wm_version=koopman.meta.version_iri, level=LEVEL)
            assert len(rows) == 50
            assert [at for at, _vec in rows] == stamps
            # 次元が保たれている（pgvector の読み戻しが壊れていないこと）
            assert rows[0][1].shape == (koopman.latent_dim,)
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_since_is_half_open(
        self, settings: Settings, koopman: KoopmanModel, world: SynthWorld
    ) -> None:
        """``after`` の行自身は返らない（右開）。

        **ここが閉区間だと、ウォーターマークを使うたびに 1 行が重複し、事象昇格が同じ
        変化点を繰り返し出す。**
        """
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            stamps = await _write_latents(connection, koopman=koopman, world=world, count=20)
            index = LatentIndex(connection)
            rows = await index.since(
                entity=PUMP,
                wm_version=koopman.meta.version_iri,
                level=LEVEL,
                after=stamps[4],
            )
            assert [at for at, _vec in rows] == stamps[5:]
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_the_limit_bounds_the_read(
        self, settings: Settings, koopman: KoopmanModel, world: SynthWorld
    ) -> None:
        """上限で打ち切る（潜在は毎秒単位で増えるので無制限に読めない）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            await _write_latents(connection, koopman=koopman, world=world, count=30)
            index = LatentIndex(connection)
            rows = await index.since(
                entity=PUMP, wm_version=koopman.meta.version_iri, level=LEVEL, limit=7
            )
            assert len(rows) == 7
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_another_level_is_not_returned(
        self, settings: Settings, koopman: KoopmanModel, world: SynthWorld
    ) -> None:
        """階層が違う潜在は返らない（同じ空間に見えて意味が違う）。"""
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            await _write_latents(connection, koopman=koopman, world=world, count=10)
            index = LatentIndex(connection)
            rows = await index.since(entity=PUMP, wm_version=koopman.meta.version_iri, level="slow")
            assert rows == []
            await connection.rollback()


class TestEndToEnd:
    """潜在索引 → プローブ → 事象昇格。"""

    @pytest.mark.asyncio
    async def test_events_are_lifted_from_indexed_latents(
        self, settings: Settings, koopman: KoopmanModel, world: SynthWorld
    ) -> None:
        """索引に入った潜在から ``inferred`` のイベントが昇格する。"""
        run, _dim = _probe_runner(world, koopman)
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            await _write_latents(connection, koopman=koopman, world=world, count=700)
            index = LatentIndex(connection)

            async def reader(
                entity: str, after: datetime | None, limit: int
            ) -> list[tuple[datetime, NDArray[np.float64]]]:
                del entity
                return await index.since(
                    entity=PUMP,
                    wm_version=koopman.meta.version_iri,
                    level=LEVEL,
                    after=after,
                    limit=limit,
                )

            sink = _Sink()
            worker = BridgeWorker(
                settings,
                entities=(PUMP,),
                reader=reader,
                probes=run,  # type: ignore[arg-type]
                sink=sink,
                rules=_RULES,
                wm_version=koopman.meta.version_iri,
                level=LEVEL,
            )
            # **1 ループでは終わらない。** `batch_size`（既定 100）ずつ読むので、変化点に
            # 届くまで複数ループ回る——実運用と同じ進み方である。最初の 200 標本は
            # すべて「稼働中」で変化点が無い（確認済み）。
            lifted = 0
            for _ in range(8):
                lifted += await worker.step()
            assert lifted >= 1, "変化点があるのに事象が昇格していない"
            assert len(sink.sent) == lifted
            # 不変条件 3: プローブ由来は必ず inferred
            assert all(event.modality is Modality.INFERRED for event in sink.sent)
            # 不変条件 1: 版を欠かさない
            assert all(event.ontology_version for event in sink.sent)
            # 証拠にプローブが残る（不変条件 7: 生スコアは evidence 側）
            kinds = {ev.kind for event in sink.sent for ev in event.evidence}
            assert "probe" in kinds
            await connection.rollback()

    @pytest.mark.asyncio
    async def test_the_second_loop_reads_only_new_latents(
        self, settings: Settings, koopman: KoopmanModel, world: SynthWorld
    ) -> None:
        """2 周目は新しい潜在だけを読む（同じ変化点を繰り返さない）。

        ウォーターマークと ``since`` の右開が噛み合っていることの確認である。
        """
        run, _dim = _probe_runner(world, koopman)
        async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as connection:
            stamps = await _write_latents(connection, koopman=koopman, world=world, count=100)
            index = LatentIndex(connection)
            reads: list[int] = []

            async def reader(
                entity: str, after: datetime | None, limit: int
            ) -> list[tuple[datetime, NDArray[np.float64]]]:
                del entity
                rows = await index.since(
                    entity=PUMP,
                    wm_version=koopman.meta.version_iri,
                    level=LEVEL,
                    after=after,
                    limit=limit,
                )
                reads.append(len(rows))
                return rows

            worker = BridgeWorker(
                settings,
                entities=(PUMP,),
                reader=reader,
                probes=run,  # type: ignore[arg-type]
                sink=_Sink(),
                rules=_RULES,
                wm_version=koopman.meta.version_iri,
                level=LEVEL,
            )
            await worker.step()
            assert worker.watermarks[PUMP] == stamps[99]
            await worker.step()
            # 2 周目は新しい潜在が無いので 0 件
            assert reads == [100, 0]
            await connection.rollback()
