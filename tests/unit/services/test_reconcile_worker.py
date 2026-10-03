"""照合ワーカーの配線のテスト（M7 の運用タスク）。

重点:

1. **様相で観測側と記録側に振り分ける** — 取り違えると、記録と観測を突き合わせているつもりで
   同じ側を 2 回見る。
2. **イベントごとに判定しない** — 持続 τ を満たすまで起票しない。
3. **記録列が無ければ検出しない** — 片側だけ見ても乖離が定義できない。
4. **照合の周期は信念更新の周期と別** — 片方を速くするために他方を遅くしない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from services.reconcile_worker import ReconcileWorker

from sbp.core import vocab
from sbp.core.belief import BooleanBelief, NumericBelief
from sbp.core.config import Settings
from sbp.core.events import EventEnvelope, ProbeEvidence, Producer, RecordEvidence, ValidTime
from sbp.core.ids import new_event_id
from sbp.core.modality import Modality
from sbp.eventlog.consumer import EventHandler
from sbp.reasoning.reconciliation.discrepancy import Discrepancy

T0 = datetime(2026, 6, 10, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
STEP = timedelta(seconds=10)
MES = "https://sbp.example/source/mes"


def _physical(*, at: datetime, p: float, fluent: str = "Running") -> EventEnvelope:
    """接地層・信念更新が出すイベント（様相 inferred）。"""
    return EventEnvelope(
        event_id=new_event_id(),
        type=f"sbp-core:PhysicalEvent/ent:{fluent}",
        subject=PUMP,
        valid_time=ValidTime(start=at, uncertainty_s=5.0),
        tx_time=at,
        modality=Modality.INFERRED,
        belief=BooleanBelief(p=p),
        evidence=(ProbeEvidence(probe_id="https://sbp.example/probe/Running/1.0.0"),),
        producer=Producer(agent="bridge@siteA", version="0.1.0"),
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


def _business(*, at: datetime, p: float, fluent: str = "Running") -> EventEnvelope:
    """統合層が出すイベント（様相 observed — 記録が存在すること自体は観測された事実）。"""
    return EventEnvelope(
        event_id=new_event_id(),
        type=f"sbp-core:BusinessEvent/ent:{fluent}",
        subject=PUMP,
        valid_time=ValidTime(start=at, uncertainty_s=0.0),
        tx_time=at,
        modality=Modality.OBSERVED,
        belief=BooleanBelief(p=p),
        evidence=(RecordEvidence(record="https://sbp.example/site/siteA/record/r-1", source=MES),),
        producer=Producer(agent="integration@siteA", version="0.1.0"),
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


class _Driver:
    """ハンドラを呼ぶ driver（``belief_worker`` のテストと同じ形）。"""

    def __init__(self, batches: list[list[EventEnvelope]]) -> None:
        self.batches = batches

    async def __call__(self, handler: EventHandler, limit: int) -> int:
        batch = self.batches.pop(0) if self.batches else []
        for event in batch[:limit]:
            await handler(event)
        return len(batch[:limit])


class _Sink:
    """起票先。"""

    def __init__(self) -> None:
        self.raised: list[Discrepancy] = []

    def __call__(self, discrepancy: Discrepancy) -> None:
        self.raised.append(discrepancy)


def _worker(
    batches: list[list[EventEnvelope]] | None = None,
) -> tuple[ReconcileWorker, _Sink]:
    sink = _Sink()
    worker = ReconcileWorker(Settings(), sink=sink)
    if batches is not None:
        worker.attach_driver(_Driver(batches))
    return worker, sink


def _diverging_window(worker: ReconcileWorker, *, samples: int = 40) -> None:
    """観測は「動いている」、記録は「止まっている」窓を作る（類型 2 の筋書き）。"""
    for index in range(samples):
        at = T0 + STEP * index
        worker.windows.observe(entity=PUMP, fluent=str(vocab.ent.Running), at=at, probability=0.98)
        worker.windows.record(entity=PUMP, fluent=str(vocab.ent.Running), at=at, probability=0.02)


class TestEventRouting:
    """様相による振り分け。"""

    @pytest.mark.asyncio
    async def test_inferred_events_fill_the_observed_column(self) -> None:
        """``inferred``（物理観測からの推定）は観測列へ。"""
        worker, _sink = _worker([[_physical(at=T0, p=0.9)]])
        await worker.step()
        window = worker.windows.window_for(entity=PUMP, fluent=str(vocab.ent.Running))
        assert len(window) == 1

    @pytest.mark.asyncio
    async def test_observed_events_fill_the_recorded_column(self) -> None:
        """``observed``（業務記録の存在）は記録列へ。

        観測の刻みが先に無いと入らない（窓の設計）ので、物理 → 業務の順に流す。
        """
        worker, _sink = _worker(
            [[_physical(at=T0, p=0.9), _physical(at=T0 + STEP, p=0.9), _business(at=T0, p=0.1)]]
        )
        await worker.step()
        series = worker.windows.window_for(entity=PUMP, fluent=str(vocab.ent.Running)).to_series()
        assert series is not None
        assert series.has_records

    @pytest.mark.asyncio
    async def test_non_fluent_events_are_ignored(self) -> None:
        """フルーエントを取り出せないイベントは窓に入れない。"""
        heartbeat = _physical(at=T0, p=0.9).model_copy(update={"type": "sbp-core:Heartbeat"})
        worker, _sink = _worker([[heartbeat]])
        await worker.step()
        assert len(worker.windows) == 0

    @pytest.mark.asyncio
    async def test_non_boolean_beliefs_are_ignored(self) -> None:
        """真偽以外の信念は照合の入力にならない（JS 距離が定義できない）。"""
        numeric = _physical(at=T0, p=0.9).model_copy(
            update={"belief": NumericBelief(mean=1.0, var=0.1)}
        )
        worker, _sink = _worker([[numeric]])
        await worker.step()
        assert len(worker.windows) == 0

    @pytest.mark.asyncio
    async def test_unknown_fluents_are_ignored(self) -> None:
        """``vocab`` に無いフルーエントは入れない（不変条件 5: 推測で IRI を作らない）。"""
        unknown = _physical(at=T0, p=0.9, fluent="NotAFluentWeKnow")
        worker, _sink = _worker([[unknown]])
        await worker.step()
        assert len(worker.windows) == 0


class TestDetectionTiming:
    """検出の周期と持続。"""

    def test_detection_is_due_on_the_first_loop(self) -> None:
        """初回は回す。"""
        worker, _sink = _worker()
        assert worker.detection_due(at=T0)

    def test_detection_is_not_due_again_immediately(self) -> None:
        """周期内は回さない。"""
        worker, _sink = _worker()
        worker.mark_detection_done(at=T0)
        assert not worker.detection_due(at=T0 + timedelta(seconds=1))

    def test_the_period_differs_from_the_belief_period(self) -> None:
        """照合の周期は信念更新（``bp_period_s``）と別の設定である。

        同じ値にすると、照合を速くするために信念更新を遅くするか、その逆をすることになる。
        """
        worker, _sink = _worker()
        settings = worker.settings.reasoning
        assert worker.detection_period == timedelta(seconds=settings.reconcile_period_s)
        assert settings.reconcile_period_s != settings.bp_period_s

    @pytest.mark.asyncio
    async def test_a_single_event_raises_nothing(self) -> None:
        """1 件のイベントでは起票しない（持続 τ を満たさない）。"""
        worker, sink = _worker([[_physical(at=T0, p=0.9)]])
        assert await worker.step() == 0
        assert sink.raised == []


class TestDetection:
    """検出と起票。"""

    @pytest.mark.asyncio
    async def test_a_sustained_divergence_is_raised(self) -> None:
        """観測と記録が τ を超えて乖離したら起票する。"""
        worker, sink = _worker([[]])
        _diverging_window(worker)
        raised = await worker.step()
        assert raised >= 1
        assert len(sink.raised) == raised
        assert worker.raised_total == raised

    @pytest.mark.asyncio
    async def test_series_without_records_are_not_detected(self) -> None:
        """記録列が無い窓は検出に渡さない。

        片側だけ見ても乖離が定義できない。**件数 0 の理由をログに出す**——統合層の
        イベントが届いていない可能性を区別できるように。
        """
        worker, sink = _worker([[]])
        for index in range(40):
            worker.windows.observe(
                entity=PUMP,
                fluent=str(vocab.ent.Running),
                at=T0 + STEP * index,
                probability=0.98,
            )
        assert await worker.step() == 0
        assert sink.raised == []

    @pytest.mark.asyncio
    async def test_agreement_raises_nothing(self) -> None:
        """観測と記録が一致していれば起票しない（陰性対照）。"""
        worker, sink = _worker([[]])
        for index in range(40):
            at = T0 + STEP * index
            worker.windows.observe(
                entity=PUMP, fluent=str(vocab.ent.Running), at=at, probability=0.98
            )
            worker.windows.record(
                entity=PUMP, fluent=str(vocab.ent.Running), at=at, probability=0.97
            )
        assert await worker.step() == 0
        assert sink.raised == []

    @pytest.mark.asyncio
    async def test_without_a_sink_detection_still_runs(self) -> None:
        """sink が無くても検出は進む（dry 相当）。

        止めると、配線の誤りが「何も起きない」としてしか現れない。
        """
        worker = ReconcileWorker(Settings())
        worker.attach_driver(_Driver([[]]))
        _diverging_window(worker)
        assert await worker.step() >= 1
        assert worker.raised_total >= 1


class TestUnconfiguredPaths:
    """設定が無いときに黙って成功しない。"""

    @pytest.mark.asyncio
    async def test_without_a_driver_nothing_is_ingested(self) -> None:
        """driver が無ければイベントを取り込まない。"""
        worker = ReconcileWorker(Settings())
        assert await worker.step() == 0
        assert len(worker.windows) == 0

    def test_the_window_covers_the_persistence_by_default(self) -> None:
        """既定設定で窓が持続 τ を覆う。

        覆えない設定だと ``WindowStore`` が構築時に落ちるので、**このテストが通ること自体が
        既定値の整合の検査になる**。
        """
        worker = ReconcileWorker(Settings())
        settings = worker.settings.reasoning
        coverable = timedelta(seconds=settings.filter_step_s) * (settings.window_samples - 1)
        assert coverable >= timedelta(seconds=settings.persistence_s)
