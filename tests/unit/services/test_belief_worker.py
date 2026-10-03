"""信念更新ワーカーの配線のテスト（M7 の運用タスク）。

**Compose を要らなくするために、イベントの取得と書き込みを関数で受ける。** ワーカーにロジックを
書くとテストが Compose 必須になる（`.claude/rules/infra-testing.md`）。ここで検査するのは
配線の性質だけ——取り込んだイベントが信念になるか、結合の周期が守られるか、source / sink が
無いときに**黙って成功しない**か。

信念更新の中身は `tests/unit/reasoning/test_ingest.py` と `test_belief.py` で検査する。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from services.belief_worker import BeliefWorker, default_ingest

from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.config import Settings
from sbp.core.events import EventEnvelope, ProbeEvidence, Producer, ValidTime
from sbp.core.ids import new_event_id
from sbp.core.modality import Modality
from sbp.eventlog.consumer import EventHandler

T0 = datetime(2026, 6, 10, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"


def _event(*, at: datetime = T0, p: float = 0.95, fluent: str = "Running") -> EventEnvelope:
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


class _Driver:
    """イベントの取得（1 回取ると次のバッチへ進む）。

    **ハンドラを呼ぶ形にしてあるのが要点である。** ポーリング（イベント列を返す）だと、
    冪等記録が副作用の前に付く実運用の性質をテストが再現しない。
    """

    def __init__(self, batches: list[list[EventEnvelope]]) -> None:
        self.batches = batches
        self.requested: list[int] = []

    async def __call__(self, handler: EventHandler, limit: int) -> int:
        self.requested.append(limit)
        batch = self.batches.pop(0) if self.batches else []
        for event in batch[:limit]:
            await handler(event)
        return len(batch[:limit])


class _Sink:
    """書き込み先。"""

    def __init__(self) -> None:
        self.written: list[Assertion] = []

    def __call__(self, assertion: Assertion) -> None:
        self.written.append(assertion)


def _worker(
    batches: list[list[EventEnvelope]] | None = None, *, with_sink: bool = True
) -> tuple[BeliefWorker, _Driver, _Sink]:
    driver = _Driver(batches if batches is not None else [])
    sink = _Sink()
    worker = BeliefWorker(
        Settings(),
        driver=driver,
        sink=sink if with_sink else None,
    )
    return worker, driver, sink


class TestEventIngestion:
    """イベントが信念になる。"""

    @pytest.mark.asyncio
    async def test_a_batch_becomes_assertions(self) -> None:
        """取り込んだイベント 1 件ごとにアサーションが 1 件出る。"""
        worker, _driver, sink = _worker([[_event(at=T0), _event(at=T0 + timedelta(seconds=10))]])
        applied = await worker.step()
        assert applied == 2
        assert len(sink.written) == 2
        assert all(row.modality is Modality.INFERRED for row in sink.written)

    @pytest.mark.asyncio
    async def test_the_batch_size_comes_from_settings(self) -> None:
        """1 ループで取る件数は Settings が決める（定数をワーカーに置かない）。"""
        worker, driver, _sink = _worker([[]])
        await worker.step()
        assert driver.requested == [worker.settings.reasoning.batch_size]

    @pytest.mark.asyncio
    async def test_the_filter_carries_state_across_loops(self) -> None:
        """ループをまたいでフィルタの状態が続く。

        前向きフィルタは「直前の事後」を持つので、1 ループで作り直すと毎回事前から始まり、
        **何回観測しても信念が動かなくなる**。
        """
        worker, _driver, _sink = _worker([[_event(at=T0)], [_event(at=T0 + timedelta(seconds=10))]])
        await worker.step()
        first = worker.ingest.filter_for(entity=PUMP, fluent="Running").posterior
        await worker.step()
        second = worker.ingest.filter_for(entity=PUMP, fluent="Running").posterior
        assert first is not None
        assert second is not None
        assert second > first

    @pytest.mark.asyncio
    async def test_skipped_events_are_not_counted_as_applied(self) -> None:
        """入れなかったイベントは戻り値に数えない（処理件数と取得件数は別）。"""
        worker, _driver, sink = _worker(
            [[_event(fluent="NotPromoted").model_copy(update={"type": "sbp-core:Heartbeat"})]]
        )
        assert await worker.step() == 0
        assert sink.written == []
        assert worker.ingest.skipped


class TestUnconfiguredPaths:
    """設定が無いときに黙って成功しない。"""

    @pytest.mark.asyncio
    async def test_without_a_driver_nothing_is_ingested(self) -> None:
        """driver が無ければ 0 件（起動時に警告を出している）。"""
        worker = BeliefWorker(Settings())
        assert await worker.step() == 0

    @pytest.mark.asyncio
    async def test_a_driver_can_be_attached_afterwards(self) -> None:
        """driver は後から差せる。

        ``KafkaEventConsumer`` はハンドラを構築時に受け取るので、
        ワーカー → consumer → driver の順でしか組めない。
        """
        worker = BeliefWorker(Settings(), sink=_Sink())
        assert await worker.step() == 0
        worker.attach_driver(_Driver([[_event()]]))
        assert await worker.step() == 1

    @pytest.mark.asyncio
    async def test_without_a_sink_the_belief_is_computed_but_not_written(self) -> None:
        """sink が無くても計算は進む（dry 相当）。

        **計算を止めないのは意図的である。** 書き込み先が無いときに計算も止めると、
        配線の誤りが「何も起きない」としてしか現れず、どちらが欠けているか分からない。
        """
        worker, _driver, sink = _worker([[_event()]], with_sink=False)
        assert await worker.step() == 1
        assert sink.written == []


class TestCouplingPeriod:
    """実体間結合を毎ループ回さない。"""

    def test_coupling_is_due_on_the_first_loop(self) -> None:
        """初回は回す（未実行の状態を「まだ早い」と扱わない）。"""
        worker, _driver, _sink = _worker()
        assert worker.coupling_due(at=T0)

    def test_coupling_is_not_due_again_immediately(self) -> None:
        """周期内は回さない（辺数に比例して重い）。"""
        worker, _driver, _sink = _worker()
        worker.mark_coupling_done(at=T0)
        assert not worker.coupling_due(at=T0 + timedelta(seconds=1))

    def test_coupling_is_due_after_the_period(self) -> None:
        """周期を過ぎたら回す。"""
        worker, _driver, _sink = _worker()
        worker.mark_coupling_done(at=T0)
        assert worker.coupling_due(at=T0 + worker.coupling_period)

    @pytest.mark.asyncio
    async def test_the_period_advances_even_with_no_events(self) -> None:
        """イベントが無くても周期判定は進む。

        取れた件数で早期 return すると、イベントが止まっている間は結合も止まり、
        「静かに何もしていない」状態になる。
        """
        worker, _driver, _sink = _worker([[]])
        await worker.step()
        # 初回で回したので、直後はもう回らない（= 周期判定が進んだ証拠）
        assert not worker.coupling_due(at=datetime.now(UTC))


class TestDefaultIngest:
    """既定の配線。"""

    def test_predicates_come_from_vocab(self) -> None:
        """述語 IRI は生成物（vocab）から取る（不変条件 5）。"""
        ingest = default_ingest(Settings())
        assert ingest.predicate_of["Running"] == str(vocab.ent.Running)

    def test_the_step_matches_the_setting(self) -> None:
        """刻み幅は Settings の値になる（滞在時間分布と尺度を合わせる）。"""
        settings = Settings()
        ingest = default_ingest(settings)
        assert ingest.transition.step == timedelta(seconds=settings.reasoning.filter_step_s)

    def test_the_ontology_version_is_stamped(self) -> None:
        """アサーションに刻む版は稼働中の版。"""
        assert default_ingest(Settings()).ontology_version == vocab.ONTOLOGY_VERSION
