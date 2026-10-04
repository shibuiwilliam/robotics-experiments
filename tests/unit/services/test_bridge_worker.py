"""接地ワーカーの配線のテスト（M7 の運用タスク）。

重点:

1. **ウォーターマークで進む** — 同じ変化点から事象を繰り返し昇格しない。
2. **送信の後にウォーターマークを進める** — 先に進めると、失敗したイベントが二度と昇格しない。
3. **1 点ではウォーターマークを進めない** — 変化点は区間で定義される。
4. **規則が無ければ落とす** — 空の事象型分布は「事象が無かった」と読まれる。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pytest
from services.bridge_worker import BridgeWorker, load_probe_set, load_rules

from sbp.bridge.calibration import TemperatureScaler
from sbp.bridge.lifting import load_event_calculus
from sbp.bridge.probes import LinearProbe, ProbeKind, ProbeMetrics, ProbeSpec
from sbp.bridge.serialization import artifact_key, bundle_probe, dumps
from sbp.core.config import Settings
from sbp.core.errors import ArtifactNotFoundError, BridgeError, DependencyUnavailableError
from sbp.core.events import EventEnvelope
from sbp.core.modality import Modality
from sbp.knowledge.artifacts import LocalArtifactStore

if TYPE_CHECKING:
    from numpy.typing import NDArray

T0 = datetime(2026, 6, 10, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
STEP = timedelta(seconds=10)
DIM = 8

# 最小の事象計算規則。``load_event_calculus`` はファクト形式
# （``initiates(事象, フルーエント).``）だけを読む——本体つきの規則は読まない。
_RULES = load_event_calculus("""
initiates(start_cmd, running).
terminates(stop_cmd, running).
""")


class _Reader:
    """潜在の読み出し（ウォーターマークを尊重する）。"""

    def __init__(self, rows: list[tuple[datetime, NDArray[np.float64]]]) -> None:
        self.rows = rows
        self.calls: list[datetime | None] = []

    async def __call__(
        self, entity: str, after: datetime | None, limit: int
    ) -> list[tuple[datetime, NDArray[np.float64]]]:
        del entity
        self.calls.append(after)
        selected = [row for row in self.rows if after is None or row[0] > after]
        return selected[:limit]


class _Sink:
    """送信先。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[EventEnvelope] = []
        self.fail = fail

    def __call__(self, event: EventEnvelope) -> None:
        if self.fail:
            raise RuntimeError("ブローカーが落ちている")
        self.sent.append(event)


def _latents(count: int) -> list[tuple[datetime, NDArray[np.float64]]]:
    """``count`` 点の潜在（値は使われないので 0 でよい）。"""
    return [(T0 + STEP * index, np.zeros(DIM)) for index in range(count)]


def _probes_switching(count: int) -> dict[str, NDArray[np.float64]]:
    """前半は成立、後半は不成立（変化点が 1 つある）プローブ出力。

    ヒステリシス（既定 0.7 / 0.3）を明確に跨ぐ値にする。
    """
    half = count // 2
    values = np.concatenate([np.full(half, 0.95), np.full(count - half, 0.05)])
    return {"Running": values}


RUNNING_PROBE = "https://sbp.example/probe/Running/1.0.0"
"""このテストが使うプローブの IRI。**ワーカーに渡す**（ワーカーは IRI を組み立てない）。"""


def _worker(
    *,
    rows: list[tuple[datetime, NDArray[np.float64]]] | None = None,
    probes: object = None,
    sink: _Sink | None = None,
    probe_ids: dict[str, str] | None = None,
    watermarks: object = None,
) -> tuple[BridgeWorker, _Reader, _Sink]:
    reader = _Reader(rows if rows is not None else [])
    used_sink = sink if sink is not None else _Sink()

    def default_probes(latents: NDArray[np.float64]) -> dict[str, NDArray[np.float64]]:
        return _probes_switching(latents.shape[0])

    worker = BridgeWorker(
        Settings(),
        entities=(PUMP,),
        reader=reader,
        probes=probes if probes is not None else default_probes,  # type: ignore[arg-type]
        sink=used_sink,
        rules=_RULES,
        wm_version="https://sbp.example/wm/koopman/1.0.0",
        probe_ids=probe_ids if probe_ids is not None else {"Running": RUNNING_PROBE},
        watermarks=watermarks,  # type: ignore[arg-type]
    )
    return worker, reader, used_sink


class TestLifting:
    """潜在から事象への昇格。"""

    @pytest.mark.asyncio
    async def test_a_change_point_becomes_an_event(self) -> None:
        """ヒステリシスを跨ぐ変化があればイベントが出る。"""
        worker, _reader, sink = _worker(rows=_latents(40))
        lifted = await worker.step()
        assert lifted >= 1
        assert len(sink.sent) == lifted

    @pytest.mark.asyncio
    async def test_the_event_is_inferred(self) -> None:
        """プローブ由来の出力は必ず ``inferred``（不変条件 3）。"""
        _worker_, _reader, sink = _worker(rows=_latents(40))
        await _worker_.step()
        assert all(event.modality is Modality.INFERRED for event in sink.sent)

    @pytest.mark.asyncio
    async def test_the_event_carries_probe_evidence(self) -> None:
        """根拠のプローブが証拠に残る（不変条件 7: 生スコアは evidence 側）。"""
        worker, _reader, sink = _worker(rows=_latents(40))
        await worker.step()
        assert sink.sent
        kinds = {evidence.kind for event in sink.sent for evidence in event.evidence}
        assert "probe" in kinds

    @pytest.mark.asyncio
    async def test_no_change_means_no_event(self) -> None:
        """変化が無ければイベントは出ない（陰性対照）。"""

        def flat(latents: NDArray[np.float64]) -> dict[str, NDArray[np.float64]]:
            return {"Running": np.full(latents.shape[0], 0.95)}

        worker, _reader, sink = _worker(rows=_latents(40), probes=flat)
        assert await worker.step() == 0
        assert sink.sent == []


class TestWatermark:
    """ウォーターマークの進み方。"""

    @pytest.mark.asyncio
    async def test_the_second_loop_reads_only_new_latents(self) -> None:
        """2 回目は前回の続きから読む（同じ変化点を繰り返さない）。"""
        worker, reader, _sink = _worker(rows=_latents(40))
        await worker.step()
        await worker.step()
        assert reader.calls[0] is None
        assert reader.calls[1] == T0 + STEP * 39

    @pytest.mark.asyncio
    async def test_a_single_sample_does_not_advance_the_watermark(self) -> None:
        """1 点では進めない。

        変化点は区間で定義されるので、1 点では判定できない。進めてしまうと、
        次のループで 2 点目が来ても 1 点目が読み直されず、変化点を見逃す。
        """
        worker, _reader, _sink = _worker(rows=_latents(1))
        assert await worker.step() == 0
        assert worker.watermarks == {}

    @pytest.mark.asyncio
    async def test_a_failing_sink_does_not_advance_the_watermark(self) -> None:
        """送信に失敗したらウォーターマークを進めない。

        **先に進めると、失敗したイベントが二度と昇格されない**（潜在は残っているが
        読み直さない）。例外を上に投げてループに扱わせる。
        """
        worker, _reader, _sink = _worker(rows=_latents(40), sink=_Sink(fail=True))
        with pytest.raises(RuntimeError, match="ブローカーが落ちている"):
            await worker.step()
        assert worker.watermarks == {}

    @pytest.mark.asyncio
    async def test_an_empty_read_leaves_the_watermark_alone(self) -> None:
        """読めるものが無ければ何もしない。"""
        worker, _reader, _sink = _worker(rows=[])
        assert await worker.step() == 0
        assert worker.watermarks == {}

    @pytest.mark.asyncio
    async def test_no_probe_output_still_advances(self) -> None:
        """プローブが何も返さない場合はウォーターマークを進める。

        進めないと同じ潜在を永久に読み続ける（プローブが無いこと自体は潜在の問題ではない）。
        その事実はログに出す。
        """

        def nothing(latents: NDArray[np.float64]) -> dict[str, NDArray[np.float64]]:
            del latents
            return {}

        worker, _reader, _sink = _worker(rows=_latents(40), probes=nothing)
        assert await worker.step() == 0
        assert worker.watermarks[PUMP] == T0 + STEP * 39


class TestRules:
    """事象計算の規則。"""

    def test_missing_rules_are_refused(self, tmp_path: Path) -> None:
        """規則が無ければ落とす。

        規則が無いと逆写像ができず ``inferred_event_type`` が空になる。空のまま流すと、
        下流は「事象型が分からない」ではなく「事象が無かった」と読む。
        """
        with pytest.raises(FileNotFoundError, match="no event calculus rules"):
            load_rules(tmp_path)

    def test_the_repository_rules_load(self) -> None:
        """リポジトリの ``ontology/process`` が実際に読める。

        **既定の引数が機能することの検査である。** 読めなければ ``BridgeWorker()`` が
        構築時に落ちる。
        """
        rules = load_rules()
        assert rules is not None


class TestUnconfiguredPaths:
    """設定が無いときに黙って成功しない。"""

    @pytest.mark.asyncio
    async def test_without_a_reader_nothing_happens(self) -> None:
        """潜在の読み出しが無ければ何もしない（起動時に警告する）。"""
        worker = BridgeWorker(Settings(), entities=(PUMP,), rules=_RULES)
        assert await worker.step() == 0

    @pytest.mark.asyncio
    async def test_without_entities_nothing_happens(self) -> None:
        """接地する実体が無ければ何もしない。

        **潜在索引の全実体を走らない。** プローブを持たない実体にも推論を走らせることになる。
        """
        reader = _Reader(_latents(40))
        worker = BridgeWorker(
            Settings(),
            entities=(),
            reader=reader,
            probes=lambda latents: _probes_switching(latents.shape[0]),
            sink=_Sink(),
            rules=_RULES,
        )
        assert await worker.step() == 0
        assert reader.calls == []


class TestProbeSupply:
    """プローブの供給（M8-02、ADR-0009）。

    重点:

    1. **置き場からロードする** — 起動時に学習する案は不変条件 10 が塞いだ（ワーカーは
       真値を読めない）。
    2. **無いものを飛ばさない** — 飛ばすと、接地しているつもりのフルーエントが静かに欠ける。
    3. **プローブ IRI を組み立てない**（不変条件 5）— 来歴に存在しない版を書かない。
    """

    @staticmethod
    def _stored_probe(
        tmp_path: Path, *, probe_id: str = RUNNING_PROBE, status: str = "active"
    ) -> LocalArtifactStore:
        """学習・較正済みのプローブを置き場に入れる。"""
        rng = np.random.default_rng(11)
        latents = rng.normal(size=(60, DIM))
        labels = latents[:, 0] > 0.0
        probe = LinearProbe(dim=DIM, seed=11).fit(latents, labels, epochs=80)
        probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents), labels))

        spec = ProbeSpec(
            probe_id=probe_id,
            grounds="https://sbp.example/core#Running",
            kind=ProbeKind.CLASSIFIER,
            wm_version="https://sbp.example/wm/koopman/1.0.0",
            status=status,  # type: ignore[arg-type]
            metrics=ProbeMetrics(auroc=0.95, ece=0.02, alignment_residual=0.04, n_samples=60),
            ontology_version="0.5.0",
        )
        store = LocalArtifactStore(tmp_path)
        store.put(artifact_key(spec), dumps(bundle_probe(probe, spec)))
        return store

    def test_a_stored_probe_becomes_a_runnable_set(self, tmp_path: Path) -> None:
        """置き場に入れたプローブがそのまま走る束になる。"""
        store = self._stored_probe(tmp_path)

        probe_set = load_probe_set(store, probe_ids=(RUNNING_PROBE,))

        assert probe_set.fluents == ("Running",)
        assert probe_set.probe_ids() == {"Running": RUNNING_PROBE}
        assert probe_set.latent_dim == DIM
        # 束は呼べる（較正を通るので値が返る = 不変条件 7 を満たしている）
        assert probe_set(np.zeros((3, DIM)))["Running"].shape == (3,)

    def test_a_missing_probe_is_an_error(self, tmp_path: Path) -> None:
        """置き場に無い IRI は例外になる（黙って飛ばさない）。"""
        store = self._stored_probe(tmp_path)

        with pytest.raises(ArtifactNotFoundError):
            load_probe_set(store, probe_ids=(RUNNING_PROBE, "https://sbp.example/probe/X/1.0.0"))

    def test_a_candidate_only_store_cannot_build_a_set(self, tmp_path: Path) -> None:
        """``candidate`` しか無ければ束が組めない（空の束を作らない）。"""
        store = self._stored_probe(tmp_path, status="candidate")

        with pytest.raises(BridgeError, match="no active probe"):
            load_probe_set(store, probe_ids=(RUNNING_PROBE,))

    def test_latest_cannot_be_requested(self, tmp_path: Path) -> None:
        """``latest`` は置き場のキーにならない（過去の説明が静かに変わる）。"""
        store = self._stored_probe(tmp_path)

        with pytest.raises(BridgeError, match="latest"):
            load_probe_set(store, probe_ids=("https://sbp.example/probe/Running/latest",))

    @pytest.mark.asyncio
    async def test_a_fluent_without_a_probe_iri_is_refused(self) -> None:
        """プローブ IRI を持たないフルーエントは昇格させない（不変条件 5）。

        以前はローカル名から IRI を組み立てていた。それは「置き場に無い版の IRI を来歴に
        書く」経路で、辿れない証拠を作る。
        """
        worker, _reader, _sink = _worker(rows=_latents(40), probe_ids={})

        with pytest.raises(BridgeError, match="no probe IRI"):
            await worker.step()


class _WatermarkSink:
    """ウォーターマークの永続化の代役（``WatermarkStore`` と同じ形）。"""

    def __init__(self, initial: dict[str, datetime] | None = None) -> None:
        self.stored: dict[str, datetime] = dict(initial or {})
        self.saves: list[tuple[str, datetime]] = []

    async def load(self) -> dict[str, datetime]:
        return dict(self.stored)

    async def save(self, entity: str, processed_through: datetime) -> None:
        # 本物と同じく後退させない
        current = self.stored.get(entity)
        if current is None or current < processed_through:
            self.stored[entity] = processed_through
        self.saves.append((entity, processed_through))


class TestWatermarkPersistence:
    """ウォーターマークの永続化（M8-03）。

    重点:

    1. **再起動で進んだ位置から再開する** — メモリだけだと、取りこぼすか重複するかになる。
    2. **副作用の後に書く** — 送信に失敗したイベントが二度と昇格されない状態を作らない。
    3. **後退させない** — 遅れて走ったループが進んだ位置を巻き戻さない。
    """

    @pytest.mark.asyncio
    async def test_the_watermark_is_persisted_after_lifting(self) -> None:
        """昇格が終わったあとにウォーターマークが書かれる。"""
        sink = _WatermarkSink()
        worker, _reader, _event_sink = _worker(rows=_latents(40), watermarks=sink)

        await worker.step()

        assert sink.stored[PUMP] == worker.watermarks[PUMP]
        assert sink.saves, "永続化が呼ばれていない"

    @pytest.mark.asyncio
    async def test_a_failing_sink_does_not_persist_the_watermark(self) -> None:
        """送信が落ちたらウォーターマークを永続化しない。

        永続化してしまうと、再起動後も「処理済み」として飛ばされ、イベントが永久に失われる。
        """
        sink = _WatermarkSink()
        worker, _reader, _event_sink = _worker(
            rows=_latents(40), sink=_Sink(fail=True), watermarks=sink
        )

        with pytest.raises(RuntimeError):
            await worker.step()

        assert sink.stored == {}, "送信失敗時に進めてはいけない"

    @pytest.mark.asyncio
    async def test_restored_watermarks_skip_already_processed_latents(self) -> None:
        """前回の位置を戻すと、その先だけを読む（再起動の再開）。"""
        rows = _latents(40)
        already = rows[19][0]
        sink = _WatermarkSink({PUMP: already})
        worker, reader, _event_sink = _worker(rows=rows, watermarks=sink)

        restored = await worker.restore_watermarks()
        await worker.step()

        assert restored == 1
        assert reader.calls == [already], "戻した位置より後だけを読む"

    @pytest.mark.asyncio
    async def test_without_a_sink_the_watermark_stays_in_memory(self) -> None:
        """永続化が無くても動く（単体テストと使い捨ての実行のため）。"""
        worker, _reader, _event_sink = _worker(rows=_latents(40))

        assert await worker.restore_watermarks() == 0
        await worker.step()
        assert PUMP in worker.watermarks

    @pytest.mark.asyncio
    async def test_run_restores_before_the_readiness_check(self) -> None:
        """``run`` はループの前にウォーターマークを戻す。

        戻さずに回すと、永続化してあっても再起動のたびに最初から読み直す（永続化が無いのと
        同じになる）。単体テストではネットワークが遮断されているので疎通検査で落ちるが、
        **その時点で既に戻っていること**を確かめれば順序は固定できる。
        """
        rows = _latents(40)
        already = rows[19][0]
        sink = _WatermarkSink({PUMP: already})
        worker, reader, _event_sink = _worker(rows=rows, watermarks=sink)

        with pytest.raises(DependencyUnavailableError):
            await worker.run(max_iterations=1)

        assert worker.watermarks[PUMP] == already, "疎通検査より前に戻っている"
        assert reader.calls == [], "疎通検査で落ちたので潜在は読んでいない"
