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
ウォーターマークは**プロセス内に持つ**ので、再起動すると進んだところから再開できない
——``TODO(question)``: 投影テーブルに逃がすか、昇格済みイベントから復元するかが未決。

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
from sbp.core import vocab
from sbp.core.config import Settings
from sbp.core.logging import get_logger
from services.runtime import ReadinessCheck, WorkerRuntime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import datetime

    from numpy.typing import NDArray

    from sbp.bridge.lifting import EventCalculusRules, LiftedEvent
    from sbp.core.events import EventEnvelope

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
            self.watermarks[entity] = timestamps[-1]
            return 0

        lifted = lift_events(
            subject=entity,
            probe_outputs=probe_outputs,
            timestamps=timestamps,
            rules=self._rules,
            settings=self.settings.bridge,
            probe_ids={name: f"https://sbp.example/probe/{name}/1.0.0" for name in probe_outputs},
            wm_version=self._wm_version,
        )
        for event in lifted:
            self._emit(event)
        self.lifted_total += len(lifted)
        # **最後まで処理してからウォーターマークを進める。** 先に進めると、送信に失敗した
        # イベントが二度と昇格されない（潜在は残っているが読み直さない）
        self.watermarks[entity] = timestamps[-1]
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

        Args:
            max_iterations: テスト用の上限。
        """
        loop = asyncio.get_running_loop()
        self.runtime.install_signal_handlers(loop)
        return await self.runtime.run(self.step, max_iterations=max_iterations)


def main() -> int:
    """エントリポイント。

    **プローブの供給元を設定していない。** 学習済みプローブの重みをどこから読むかが未決で
    （レジストリは場所と版の管理だけを行い、重みの直列化は未実装——``docs/PROGRESS.md`` の
    既知の制約）、``TODO(question)``: S3 から読むか、プロセス起動時に学習するかを決める必要が
    ある。接地する実体の一覧も、知識グラフから引く（型で絞る）か設定で渡すかが未決。
    """
    worker = BridgeWorker()
    asyncio.run(worker.run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
