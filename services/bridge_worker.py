"""接地ワーカー（M7 の運用タスク）。

推定パス（設計書 §2.5）の後半を回す: **潜在 z → プローブ → 事象昇格 → イベントログ**。

**入力は潜在索引である**（設計書 §6.2 の latent 層）。符号化器は L0（エッジ）、プローブと
事象昇格は L1（拠点）という配置（§4.7）なので、潜在が両者の境界を越える。raw 層（オブジェクト
ストア）から読むのは WM の MAJOR 更新で再潜在化するときだけで、常用の経路ではない。

**潜在をイベントログに載せない。** L0 は 100 ms 未満の階層で、潜在は数値ベクトルである。
イベントログは「誰かの主張」を運ぶ場所で、潜在は主張ではない——載せると量に見合わないうえ、
``EventEnvelope`` の意味（様相・確信度・来歴）が潜在に対して定義できない。

**ウォーターマークで進む。** 「最後に処理した潜在の時刻」を実体ごとに持ち、それより後だけを
読む（``LatentIndex.since`` は右開）。これが無いと、同じ変化点から事象を繰り返し昇格する。
ウォーターマークは**投影表に永続化する**（M8-03、``sbp.knowledge.watermarks``）ので、再起動
しても進んだところから再開する。永続化が無い（``watermarks=None``）場合はプロセス内だけに
持ち、再起動で最初から読み直す——単体テストと使い捨ての実行のための形である。

**プローブは置き場から版指定でロードする**（ADR-0009）。起動時に学習する案は不変条件 10 が
塞いだ——ワーカーは真値を読めないので、そもそも学習できない。接地する実体は知識グラフから
型で引き、明示で渡せば上書きできる（カナリアのため）。

**このワーカーは永続化しない**（``bridge`` 層の規約）。潜在索引は読むだけで、出力はイベント
だけである。読み出しが ``services`` 側にあるのは、``bridge`` が ``knowledge`` を import
できないため（import-linter の契約）——層の境界がワーカーの形に出ている。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from sbp.bridge.lifting import lift_events, load_event_calculus
from sbp.bridge.probes import ProbeKind, ProbeSpec
from sbp.bridge.runner import ProbeSet
from sbp.bridge.serialization import artifact_key, loads, restore_probe
from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.errors import BridgeError, ConfigError
from sbp.core.logging import get_logger
from services.runtime import ReadinessCheck, WorkerRuntime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import datetime

    from numpy.typing import NDArray

    from sbp.bridge.lifting import EventCalculusRules, LiftedEvent
    from sbp.bridge.probes import LinearProbe
    from sbp.core.events import EventEnvelope
    from sbp.eventlog.producer import KafkaEventProducer
    from sbp.knowledge.artifacts import ArtifactStore
    from sbp.knowledge.watermarks import WatermarkStore

    WatermarkSink = WatermarkStore
    """ウォーターマークの永続化。

    具体型をそのまま使う（Protocol を切らない）。単体テストは同じ形の代役を渡すので、
    抽象を 1 つ増やすより読みやすい——``load`` / ``save`` の 2 メソッドしかない。
    """

    LatentReader = Callable[
        [str, datetime | None, int], Awaitable[Sequence[tuple[datetime, NDArray[np.float64]]]]
    ]
    """``(実体, この時刻より後, 最大件数) -> (時刻, 潜在)`` の列。

    ``LatentIndex.since`` を包む。関数で受けるのは、単体テストを Compose 無しで回すため。
    """
    ProbeRunner = Callable[[NDArray[np.float64]], Mapping[str, NDArray[np.float64]]]
    """``潜在の行列 -> フルーエント名 → 較正済み確率の列``。

    **較正済みの値だけを返すこと**（不変条件 7）。生スコアは ``evidence`` に残す。
    """
    EventSink = Callable[[EventEnvelope], object]
    """イベント 1 件を送る。"""

log = get_logger(__name__)

WORKER_NAME = "bridge-worker"

_PROCESS_RULES = Path("ontology/process")
"""事象計算の規則の置き場（``initiates`` / ``terminates``）。"""


def readiness_checks(settings: Settings) -> tuple[ReadinessCheck, ...]:
    """このワーカーの依存（潜在索引とイベントログ）。

    Args:
        settings: 設定。
    """
    from services.belief_worker import tcp_reachable  # noqa: PLC0415 — 疎通検査を共有する

    return (ReadinessCheck(name="redpanda", probe=lambda: tcp_reachable(settings.kafka_bootstrap)),)


def load_rules(directory: Path = _PROCESS_RULES) -> EventCalculusRules:
    """``ontology/process/*.lp`` から事象計算の規則を読む。

    **規則が 1 本も無ければ落とす。** 規則が無いと逆写像（どの事象型が起きたか）ができず、
    昇格したイベントの ``inferred_event_type`` が空になる。空のまま流すと、下流は
    「事象型が分からない」ではなく「事象が無かった」と読む。

    Args:
        directory: 規則ファイルの置き場。

    Returns:
        結合した規則。

    Raises:
        FileNotFoundError: 規則ファイルが無い場合。
    """
    files = sorted(directory.glob("*.lp"))
    if not files:
        raise FileNotFoundError(
            f"no event calculus rules under {directory}; lifting cannot infer event types "
            "and downstream would read an empty distribution as 'no event'"
        )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    return load_event_calculus(combined)


class BridgeWorker:
    """潜在の取り込みと事象昇格。

    Attributes:
        settings: 設定。
        runtime: 実行ループ。
        watermarks: 実体 → 最後に処理した潜在の時刻。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        entities: Sequence[str] = (),
        reader: LatentReader | None = None,
        probes: ProbeRunner | None = None,
        sink: EventSink | None = None,
        rules: EventCalculusRules | None = None,
        wm_version: str | None = None,
        level: str = "mid",
        probe_ids: Mapping[str, str] | None = None,
        watermarks: WatermarkSink | None = None,
    ) -> None:
        """Args:
        settings: 設定（既定は環境から読む）。
        entities: 接地する実体の IRI。**明示で渡す**——潜在索引の全実体を走ると、
            プローブを持たない実体にも推論を走らせることになる。
        reader: 潜在の読み出し（``None`` なら取り込まない）。
        probes: プローブの実行（``None`` なら昇格しない）。
        sink: イベントの送信（``None`` なら送らない）。
        rules: 事象計算の規則（``None`` なら ``ontology/process`` から読む）。
        wm_version: 潜在を作った WM 版の IRI（証拠に残す）。
        level: 潜在の階層。既定は ``mid``（設備状態が最も素直に出る階層）。
        probe_ids: フルーエントのローカル名 → プローブ IRI。昇格したイベントの証拠に
            「どのプローブが言ったか」を残す。**IRI を組み立てない**（不変条件 5）
            ——``ProbeSet.probe_ids()`` から渡す。
        watermarks: ウォーターマークの永続化（``None`` ならプロセス内だけに持つ）。
            起動時に :meth:`restore_watermarks` を呼んで前回の位置を戻す。
        """
        self.settings = settings or Settings()
        self.runtime = WorkerRuntime(
            name=WORKER_NAME,
            settings=self.settings,
            checks=readiness_checks(self.settings),
        )
        self._entities = tuple(entities)
        self._reader = reader
        self._probes = probes
        self._sink = sink
        self._rules = rules if rules is not None else load_rules()
        self._wm_version = wm_version
        self._level = level
        self._probe_ids = dict(probe_ids or {})
        self._watermark_sink = watermarks
        self.watermarks: dict[str, datetime] = {}
        self.lifted_total = 0
        if not self._entities:
            log.warning(
                "worker.no_entities",
                worker=WORKER_NAME,
                detail="接地する実体が無いので何も昇格しない",
            )
        if reader is None or probes is None:
            log.warning(
                "worker.not_wired",
                worker=WORKER_NAME,
                reader=reader is not None,
                probes=probes is not None,
                detail="reader と probes の両方が無いと昇格できない",
            )

    async def restore_watermarks(self) -> int:
        """永続化されたウォーターマークをプロセス内に戻す（起動時に 1 回）。

        **知らない実体の分も戻す。** 接地対象から外れた実体のウォーターマークが残っていても
        害は無く、再び対象に入ったときに続きから読める。逆に対象の実体の値を落とすと、
        その実体だけ最初から読み直す。

        Returns:
            戻した件数。
        """
        if self._watermark_sink is None:
            return 0
        restored = await self._watermark_sink.load()
        self.watermarks.update(restored)
        log.info(
            "worker.watermarks_restored",
            worker=WORKER_NAME,
            restored=len(restored),
            entities=len(self._entities),
        )
        return len(restored)

    async def _advance(self, entity: str, through: datetime) -> None:
        """ウォーターマークを進め、永続化する。

        **副作用の後に呼ぶ。** 先に進めると、送信に失敗したイベントが二度と昇格されない
        （潜在は残っているが読み直さない。所見 52）。

        Args:
            entity: 実体 IRI。
            through: この時刻までを処理した。
        """
        self.watermarks[entity] = through
        if self._watermark_sink is not None:
            await self._watermark_sink.save(entity, through)

    async def step(self) -> int:
        """1 ループ。各実体の新しい潜在を取り込んで事象を昇格する。

        Returns:
            昇格したイベントの件数。
        """
        if self._reader is None or self._probes is None:
            return 0
        total = 0
        for entity in self._entities:
            total += await self._ground(entity)
        if total:
            log.info(
                "worker.bridge_cycle",
                worker=WORKER_NAME,
                entities=len(self._entities),
                lifted=total,
            )
        return total

    async def _ground(self, entity: str) -> int:
        """1 実体ぶんを接地する。

        Args:
            entity: 実体 IRI。

        Returns:
            昇格した件数。
        """
        assert self._reader is not None  # step() が確認済み
        assert self._probes is not None
        rows = list(
            await self._reader(
                entity, self.watermarks.get(entity), self.settings.reasoning.batch_size
            )
        )
        if len(rows) < 2:
            # 1 点では変化点が定義できない（事象昇格は区間を見る）。**ウォーターマークを
            # 進めない**——次のループで 2 点目が来たときに一緒に処理する
            return 0

        timestamps = [at for at, _vector in rows]
        latents = np.vstack([vector for _at, vector in rows])
        probe_outputs = self._probes(latents)
        if not probe_outputs:
            log.info(
                "worker.no_probe_outputs",
                worker=WORKER_NAME,
                entity=entity,
                detail="プローブが 1 つも結果を返さなかった（active なプローブが無い可能性）",
            )
            await self._advance(entity, timestamps[-1])
            return 0

        # **プローブ IRI は束ねが持っているものを使う**（不変条件 5: IRI を組み立てない）。
        # 以前はローカル名から文字列で組んでいたが、それは「存在しない版の IRI を来歴に
        # 書く」経路だった——説明を辿れない証拠は証拠ではない
        missing = sorted(set(probe_outputs) - set(self._probe_ids))
        if missing:
            raise BridgeError(
                f"no probe IRI for fluents {missing}; lifted events would carry a fabricated "
                "provenance that cannot be traced back to a stored probe version"
            )
        lifted = lift_events(
            subject=entity,
            probe_outputs=probe_outputs,
            timestamps=timestamps,
            rules=self._rules,
            settings=self.settings.bridge,
            probe_ids={name: self._probe_ids[name] for name in probe_outputs},
            wm_version=self._wm_version,
        )
        for event in lifted:
            self._emit(event)
        self.lifted_total += len(lifted)
        # **最後まで処理してからウォーターマークを進める。** 先に進めると、送信に失敗した
        # イベントが二度と昇格されない（潜在は残っているが読み直さない）
        await self._advance(entity, timestamps[-1])
        return len(lifted)

    def _emit(self, event: LiftedEvent) -> None:
        """昇格したイベントを送る。``sink`` が無ければ送らない。"""
        if self._sink is None:
            return
        envelope = event.to_envelope(
            site_id=self.settings.site_id,
            # 逆写像で最も確率の高い事象型を型 IRI にする。**分布は証拠として残る**ので、
            # 下流は最尤の型だけでなく候補全体を見られる
            event_type_iri=f"sbp-core:PhysicalEvent/{event.fluent}",
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        self._sink(envelope)

    async def run(self, *, max_iterations: int | None = None) -> int:
        """起動する。

        **ループの前にウォーターマークを戻す。** 戻さずに回すと、永続化してあっても再起動の
        たびに最初から読み直す（永続化が無いのと同じになる）。

        Args:
            max_iterations: テスト用の上限。
        """
        await self.restore_watermarks()
        loop = asyncio.get_running_loop()
        self.runtime.install_signal_handlers(loop)
        return await self.runtime.run(self.step, max_iterations=max_iterations)


def load_probe_set(
    store: ArtifactStore,
    *,
    probe_ids: Sequence[str],
    site: str | None = None,
    operating_mode: str | None = None,
) -> ProbeSet:
    """置き場からプローブを読み、``active`` な束を組む（ADR-0009）。

    **版は ``probe_ids`` が持つ。** 置き場のキーは IRI の末尾 2 節から組まれるので
    （:func:`sbp.bridge.serialization.artifact_key`）、``latest`` を渡すと落ちる。

    **1 本も読めなければ落ちる。** :class:`~sbp.bridge.runner.ProbeSet` が空の束を拒否する
    ——空の束で接地を続けると「変化が無かった」と区別できない。

    Args:
        store: 成果物の置き場。
        probe_ids: ロードするプローブの IRI（版を含む）。
        site: 拠点識別子（``validity_sites`` で絞る）。
        operating_mode: 運転モード（``operating_modes`` で絞る）。

    Returns:
        走らせる束。

    Raises:
        ArtifactNotFoundError: 置き場に無い IRI を指定した場合。**無いものを飛ばさない**
            ——飛ばすと、接地しているつもりのフルーエントが静かに欠ける。
    """
    entries: list[tuple[LinearProbe, ProbeSpec]] = []
    for probe_id in probe_ids:
        key = artifact_key(_spec_stub(probe_id))
        bundle = loads(store.get(key))
        entries.append(restore_probe(bundle))
    probe_set = ProbeSet(entries, site=site, operating_mode=operating_mode)
    log.info(
        "worker.probes_loaded",
        worker=WORKER_NAME,
        requested=len(probe_ids),
        active=len(probe_set),
        fluents=probe_set.fluents,
        wm_version=probe_set.wm_version,
    )
    return probe_set


def _spec_stub(probe_id: str) -> ProbeSpec:
    """キーを組むためだけの最小の :class:`ProbeSpec`。

    :func:`sbp.bridge.serialization.artifact_key` は ``probe_id`` しか見ないが、引数は
    ``ProbeSpec`` である（キーの組み方を 1 箇所に置くため）。置き場から読む前は実体の
    メタデータを持っていないので、ここで最小のものを作る。

    Args:
        probe_id: プローブの IRI（版を含む）。
    """
    return ProbeSpec(
        probe_id=probe_id,
        grounds=probe_id,
        kind=ProbeKind.CLASSIFIER,
        wm_version="urn:sbp:unknown",
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


async def build_worker(
    settings: Settings | None = None,
    *,
    site_id: str,
    probe_ids: Sequence[str],
    wm_version: str,
    level: str = "mid",
    ground_types: Sequence[str] = (vocab.core.Asset,),
    entities: Sequence[str] = (),
) -> tuple[BridgeWorker, KafkaEventProducer]:
    """実運用の配線を組む（潜在索引 → プローブ → 事象昇格 → Redpanda）。

    Args:
        settings: 設定（既定は環境から読む）。
        site_id: 拠点識別子（トピック名と実体の絞り込みに使う）。
        probe_ids: ロードするプローブの IRI（版を含む）。
        wm_version: 読む潜在を作った WM 版の IRI。**プローブの ``wm_version`` と一致する
            こと**を検査する——違う潜在空間のプローブを走らせると、次元が合えば動いてしまう。
        level: 潜在の階層。
        ground_types: 接地対象の型 IRI（``vocab`` から渡す。不変条件 5）。
        entities: 接地する実体を明示で指定する（カナリア用）。空なら知識グラフから引く。

    Returns:
        ``(ワーカー, producer)``。呼び出し側が ``producer.stop()`` を担う。

    Raises:
        BridgeError: プローブの束の ``wm_version`` が引数と食い違う場合。
    """
    import psycopg  # noqa: PLC0415 — 実運用の配線でのみ使う

    from sbp.core.events import EventLayer, topic_name  # noqa: PLC0415
    from sbp.eventlog.producer import KafkaEventProducer  # noqa: PLC0415
    from sbp.knowledge import (  # noqa: PLC0415
        HttpGraphStore,
        LatentIndex,
        S3ArtifactStore,
        WatermarkStore,
        entities_of_type,
    )

    resolved = settings or Settings()
    probe_set = load_probe_set(
        S3ArtifactStore.from_settings(resolved),
        probe_ids=probe_ids,
        site=site_id,
    )
    if probe_set.wm_version != wm_version:
        raise BridgeError(
            f"probe set belongs to {probe_set.wm_version} but the worker was asked to read "
            f"latents of {wm_version}; the same latent matrix would be read in two spaces "
            "(design §4.8)"
        )

    targets = tuple(entities)
    if not targets:
        targets = tuple(
            entities_of_type(HttpGraphStore(resolved), types=ground_types, site=site_id)
        )

    connection = await psycopg.AsyncConnection.connect(resolved.pg_dsn, autocommit=True)
    index = LatentIndex(connection)
    watermarks = WatermarkStore(connection, worker=WORKER_NAME, wm_version=wm_version, level=level)

    async def reader(
        entity: str, after: datetime | None, limit: int
    ) -> Sequence[tuple[datetime, NDArray[np.float64]]]:
        """潜在索引から 1 実体ぶんを読む。"""
        return await index.since(
            entity=entity, wm_version=wm_version, level=level, after=after, limit=limit
        )

    producer = KafkaEventProducer(resolved)
    await producer.start()
    topic = topic_name(site_id, EventLayer.BRIDGE, "physical")

    def sink(event: EventEnvelope) -> object:
        """昇格したイベントを送る（送信の完了は producer のフラッシュで待つ）。"""
        return asyncio.create_task(producer.send(topic, event))

    worker = BridgeWorker(
        resolved,
        entities=targets,
        reader=reader,
        probes=probe_set,
        sink=sink,
        wm_version=wm_version,
        level=level,
        probe_ids=probe_set.probe_ids(),
        watermarks=watermarks,
    )
    log.info(
        "worker.wired",
        worker=WORKER_NAME,
        site_id=site_id,
        entities=len(targets),
        probes=len(probe_set),
        topic=topic,
    )
    return worker, producer


def main() -> int:
    """エントリポイント。

    プローブの IRI と WM 版は環境から渡す（``SBP_BRIDGE_PROBE_IDS`` / ``SBP_BRIDGE_WM_VERSION``
    を読む ``Settings.bridge`` の項目）。**既定値を置かない**——適当な版を既定にすると、
    設定漏れが「別の版のプローブで接地していた」という形で現れる（ADR-0009）。
    """

    async def _run() -> None:
        settings = Settings()
        if not settings.bridge.probe_ids or not settings.bridge.wm_version:
            raise ConfigError(
                "bridge-worker needs SBP_BRIDGE_PROBE_IDS and SBP_BRIDGE_WM_VERSION; "
                "without them there is no probe to run and no latent space to read "
                "(ADR-0009). A default version here would silently ground with the wrong one."
            )
        worker, producer = await build_worker(
            settings,
            site_id=settings.site_id,
            probe_ids=settings.bridge.probe_ids,
            wm_version=settings.bridge.wm_version,
        )
        try:
            await worker.run()
        finally:
            await producer.stop()

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
