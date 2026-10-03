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
from services.bridge_worker import BridgeWorker, load_rules

from sbp.bridge.lifting import load_event_calculus
from sbp.core.config import Settings
from sbp.core.events import EventEnvelope
from sbp.core.modality import Modality

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


def _worker(
    *,
    rows: list[tuple[datetime, NDArray[np.float64]]] | None = None,
    probes: object = None,
    sink: _Sink | None = None,
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
