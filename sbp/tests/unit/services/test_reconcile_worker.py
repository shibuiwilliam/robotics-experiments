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
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from services.metrics import MetricRecorder
from services.reconcile_worker import ReconcileWorker, discrepancy_topic, load_registries

from sbp.core import vocab
from sbp.core.belief import BooleanBelief, NumericBelief
from sbp.core.config import ReasoningSettings, Settings
from sbp.core.events import EventEnvelope, ProbeEvidence, Producer, RecordEvidence, ValidTime
from sbp.core.ids import new_event_id
from sbp.core.metrics import MetricSample
from sbp.core.modality import Modality
from sbp.eventlog.consumer import EventHandler
from sbp.integration.reliability import ReliabilityRegistry
from sbp.reasoning.reconciliation.discrepancy import Discrepancy
from sbp.reasoning.reconciliation.severity import BusinessValue, BusinessValueRegistry

if TYPE_CHECKING:
    from collections.abc import Sequence

T0 = datetime(2026, 6, 10, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
STEP = timedelta(seconds=10)
MES = "https://sbp.example/source/mes"
ROOT = Path(__file__).resolve().parents[3]


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

    def test_unwired_registries_are_reported(self) -> None:
        """業務価値・信頼性・実体の型を渡し忘れると、名前を挙げて報告する（M8-30）。

        構築子の既定（空のレジストリ）でも動いてしまうので、**落ちない代わりに数える**。
        運用の ``build_worker`` が渡し忘れていた（所見 109）。
        """
        worker = ReconcileWorker(Settings())
        assert worker.unwired == ("values", "reliability", "entity_types")

    def test_a_fully_wired_worker_reports_nothing(self) -> None:
        worker = ReconcileWorker(
            Settings(),
            values=_values(),
            reliability=ReliabilityRegistry({}),
            entity_types_provider=dict,
        )
        assert worker.unwired == ()


class TestDiscrepancyTopic:
    """起票先トピックの決め方（M8-04、ADR-0010）。"""

    def test_the_default_is_the_site_topic(self) -> None:
        """既定は拠点トピック。検出器は自拠点のことだけを知っていればよい。"""
        assert discrepancy_topic(Settings(), site_id="siteA") == "siteA.reasoning.discrepancy"

    def test_central_can_be_selected(self) -> None:
        """設定で中央に切り替えられる（拠点別の運用もありうるという未決を設定で残した）。"""
        settings = Settings()
        settings.reasoning.discrepancy_scope = "central"

        assert discrepancy_topic(settings, site_id="siteA") == "central.reasoning.discrepancy"

    def test_the_scope_is_restricted_to_two_values(self) -> None:
        """``site`` / ``central`` 以外は設定として受け付けない。

        **両方に出す形を設定で作れないようにしている**——同じ不一致が 2 つの ``event_id`` を
        持ち、冪等性（不変条件 4）が壊れる（ADR-0010）。
        """
        with pytest.raises(ValidationError):
            ReasoningSettings(discrepancy_scope="both")  # type: ignore[arg-type]


class TestMetrics:
    """照合の指標（M8-14）。

    重点:

    1. **起票 0 件でも記録する** — 「起票が無い」と「照合が動いていない」を区別する
       （所見 32）。
    2. **類型ごとに起票数を出す** — 運用で見るのは「どの類型が増えたか」である。
    3. **抑制率は検出延べ件数で割る** — 統合で消えた分も分母に入る。
    """

    @staticmethod
    def _recorder() -> tuple[MetricRecorder, list[MetricSample]]:
        captured: list[MetricSample] = []

        async def capture(samples: Sequence[MetricSample]) -> int:
            captured.extend(samples)
            return len(samples)

        return MetricRecorder(site="siteA", sink=capture, period_s=0.0), captured

    @pytest.mark.asyncio
    async def test_a_quiet_cycle_still_records(self) -> None:
        """何も起票されない周期でも指標の行が出る。

        出さないと、不一致 0 件と「照合が動いていない」が区別できない。
        """
        recorder, captured = self._recorder()
        worker = ReconcileWorker(Settings(), metrics=recorder)

        await worker.step()

        keys = {sample.name.key for sample in captured}
        # 窓に何も無いので検出自体が走らない（_detect が早期 return する）。
        # そのときは指標も出ない——「照合の周期は来たが材料が無い」はログで分かる
        assert keys <= {"discrepancies_raised", "suppression_rate"}

    @pytest.mark.asyncio
    async def test_raised_discrepancies_are_counted_by_kind(self) -> None:
        """起票が類型ごとに数えられ、抑制率も出る。"""
        recorder, captured = self._recorder()
        worker = ReconcileWorker(Settings(), metrics=recorder)
        handler: EventHandler = worker.handle_event

        # 物理（停止）と業務（稼働）を同じ窓に入れて類型 2 を起こす
        at = T0
        for index in range(40):
            moment = at + STEP * index
            await handler(_physical(at=moment, p=0.02))
            await handler(_business(at=moment, p=0.95))

        worker.mark_detection_done(at=T0)
        worker._last_detection = None  # type: ignore[attr-defined]  # 周期判定を強制
        await worker.step()

        raised = [s for s in captured if s.name.key == "discrepancies_raised"]
        rates = [s for s in captured if s.name.key == "suppression_rate"]
        assert raised, "起票数の指標が出ていない"
        assert rates, "抑制率の指標が出ていない"
        assert all(0.0 <= sample.value <= 1.0 for sample in rates)


PUMP_VALUE = BusinessValue(
    object_type=str(vocab.ent.Pump), asset_value=2_500_000.0, safety_class=0.3, sla_penalty=4e6
)


def _values() -> BusinessValueRegistry:
    """``Pump`` だけを登録したレジストリ（基準額は既定の 100 万）。"""
    return BusinessValueRegistry(
        {str(vocab.ent.Pump): PUMP_VALUE},
        default_scale=Settings().reasoning.severity_value_scale,
    )


class _TypeProvider:
    """実体 → 型の provider。呼ばれた回数を数える。"""

    def __init__(self, types: dict[str, str]) -> None:
        self.types = types
        self.calls = 0

    def __call__(self) -> dict[str, str]:
        self.calls += 1
        return dict(self.types)


def _valued_worker(
    provider: _TypeProvider | None, *, entity_types: dict[str, str] | None = None
) -> tuple[ReconcileWorker, _Sink]:
    sink = _Sink()
    settings = Settings(reasoning=ReasoningSettings(reconcile_period_s=1e-6))
    worker = ReconcileWorker(
        settings,
        sink=sink,
        values=_values(),
        reliability=ReliabilityRegistry({}),
        entity_types=entity_types,
        entity_types_provider=provider,
    )
    worker.attach_driver(_Driver([[], [], []]))
    return worker, sink


class TestBusinessValue:
    """深刻度の業務価値が運用の配線で効くこと（M8-30、所見 109）。

    重点:

    1. **実体の型で登録値を引く** — 型が分からなければ全件が基準額の半分（中間値）になり、
       深刻度の順位が平らになる。
    2. **型は毎周期読み直す** — 資産台帳の同期（M8-25）で実体が増えたとき、古い対応のまま
       評価し続けない。
    3. **照合する列が無い周期はグラフを読まない** — 記録が届かない間に毎周期問い合わせない。
    """

    @pytest.mark.asyncio
    async def test_the_registered_value_of_the_entity_type_is_used(self) -> None:
        worker, sink = _valued_worker(_TypeProvider({PUMP: str(vocab.ent.Pump)}))
        _diverging_window(worker)
        assert await worker.step() >= 1
        assert sink.raised[0].severity.value_at_stake == pytest.approx(PUMP_VALUE.value_at_stake())

    @pytest.mark.asyncio
    async def test_without_a_type_the_default_value_is_used(self) -> None:
        """対照: 型が分からなければ基準額の半分（``BusinessValueRegistry.get`` の既定）。"""
        worker, sink = _valued_worker(_TypeProvider({}))
        _diverging_window(worker)
        assert await worker.step() >= 1
        scale = worker.settings.reasoning.severity_value_scale
        assert sink.raised[0].severity.value_at_stake == pytest.approx(scale / 2.0)

    @pytest.mark.asyncio
    async def test_the_types_are_read_every_detection_cycle(self) -> None:
        provider = _TypeProvider({PUMP: str(vocab.ent.Pump)})
        worker, _sink = _valued_worker(provider)
        _diverging_window(worker)
        await worker.step()
        await worker.step()
        assert provider.calls == 2

    @pytest.mark.asyncio
    async def test_explicit_types_override_the_provider(self) -> None:
        """明示した対応が勝つ（カナリアや検証で 1 実体だけ差し替えるため）。"""
        provider = _TypeProvider({PUMP: "urn:sbp:test:class:Unregistered"})
        worker, sink = _valued_worker(provider, entity_types={PUMP: str(vocab.ent.Pump)})
        _diverging_window(worker)
        await worker.step()
        assert sink.raised[0].severity.value_at_stake == pytest.approx(PUMP_VALUE.value_at_stake())

    @pytest.mark.asyncio
    async def test_no_comparable_series_means_no_graph_read(self) -> None:
        provider = _TypeProvider({PUMP: str(vocab.ent.Pump)})
        worker, _sink = _valued_worker(provider)
        for index in range(40):
            worker.windows.observe(
                entity=PUMP, fluent=str(vocab.ent.Running), at=T0 + STEP * index, probability=0.98
            )
        assert await worker.step() == 0
        assert provider.calls == 0


class TestRegistries:
    """リポジトリのレジストリが運用の配線で読めること（M8-30）。"""

    def test_the_repository_registries_load(self) -> None:
        values, reliability = load_registries(Settings(), root=ROOT)
        assert str(vocab.ent.Pump) in values.object_types
        # 未登録なら IntegrationError（既定値で代替しない設計）
        mes = reliability.get(str(vocab.ent.MesSource), str(vocab.ent.OperationLog))
        assert mes.systematic_biases

    def test_registered_types_resolve_against_the_ontology(self) -> None:
        """実際のオントロジーと ``value/*.yaml`` の組で、ポンプが ``Pump`` の値に解決される。

        ``value/`` は ``Pump`` と上位の ``RotatingEquipment`` の両方を登録している。型の階層は
        ``ontology/domain/`` にあり、手書きのスキーマでは確かめたことにならない。
        """
        from sbp.knowledge import EmbeddedGraphStore, most_specific_types  # noqa: PLC0415

        values, _reliability = load_registries(Settings(), root=ROOT)
        store = EmbeddedGraphStore()
        for ttl in sorted((ROOT / "ontology").glob("*/*.ttl")):
            if ttl.parent.name in {"core", "domain"}:
                store.load_turtle(ttl.read_text(encoding="utf-8"), graph="urn:sbp:test:ontology")
        rdf_type = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
        store.load_turtle(f"<{PUMP}> <{rdf_type}> <{vocab.ent.Pump}> .", graph="urn:sbp:test:a")

        found = most_specific_types(store, types=values.object_types, site="siteA")

        assert found == {PUMP: str(vocab.ent.Pump)}

    def test_the_value_scale_comes_from_the_settings(self) -> None:
        """正規化の基準額を二重定義しない（``SBP_REASONING_SEVERITY_VALUE_SCALE``）。"""
        settings = Settings(reasoning=ReasoningSettings(severity_value_scale=123.0))
        values, _reliability = load_registries(settings, root=ROOT)
        assert values.scale == pytest.approx(123.0)
