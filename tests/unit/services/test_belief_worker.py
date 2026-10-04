"""信念更新ワーカーの配線のテスト（M7 の運用タスク）。

**Compose を要らなくするために、イベントの取得と書き込みを関数で受ける。** ワーカーにロジックを
書くとテストが Compose 必須になる（`.claude/rules/infra-testing.md`）。ここで検査するのは
配線の性質だけ——取り込んだイベントが信念になるか、結合の周期が守られるか、source / sink が
無いときに**黙って成功しない**か。

信念更新の中身は `tests/unit/reasoning/test_ingest.py` と `test_belief.py` で検査する。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest
from services.belief_worker import BeliefWorker, default_ingest
from services.metrics import MetricRecorder

from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.config import Settings
from sbp.core.events import EventEnvelope, ProbeEvidence, Producer, ValidTime
from sbp.core.ids import new_event_id
from sbp.core.modality import Modality
from sbp.core.time import now
from sbp.eventlog.consumer import EventHandler
from sbp.reasoning.coupling import AggregationRule, PartWhole, SituationLink

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


LINE = "https://sbp.example/site/siteA/asset/LINE-1"
PUMP_B = "https://sbp.example/site/siteA/asset/P-102"


def _event_for(entity: str, *, at: datetime, p: float) -> EventEnvelope:
    return _event(at=at, p=p).model_copy(update={"subject": entity})


class TestCouplingCycle:
    """結合の周期で信念が伝播する（設計書 §8.1）。"""

    def _relations(
        self, priors: Mapping[tuple[str, str], float]
    ) -> tuple[list[PartWhole], list[SituationLink]]:
        """LINE-1 は P-101 と P-102 の全体（Running は all 集約）。Situation の結合は無し。"""
        fluent = str(vocab.ent.Running)
        parts = tuple(entity for entity, name in priors if name == fluent and entity != LINE)
        if (LINE, fluent) not in priors or not parts:
            return [], []
        return [PartWhole(whole=LINE, parts=parts, fluent=fluent, rule=AggregationRule.ALL)], []

    @pytest.mark.asyncio
    async def test_coupling_writes_adjusted_beliefs(self) -> None:
        """部分が停止していれば、全体（ライン）の信念が下がって書かれる。

        all 集約なので「どのポンプも動いていなければラインは動いていない」。
        """
        sink = _Sink()
        worker = BeliefWorker(Settings(), sink=sink, relations=self._relations)
        # ライン自身は「動いている」と観測され、部分は 2 台とも「止まっている」
        worker.attach_driver(
            _Driver(
                [
                    [
                        _event_for(LINE, at=T0, p=0.9),
                        _event_for(PUMP, at=T0, p=0.02),
                        _event_for(PUMP_B, at=T0, p=0.02),
                    ]
                ]
            )
        )
        await worker.step()

        line_rows = [row for row in sink.written if row.subject == LINE]
        # 1 件目は前向きフィルタの出力、2 件目は結合後の調整
        assert len(line_rows) == 2
        beliefs = [row.belief for row in line_rows]
        assert all(isinstance(belief, BooleanBelief) for belief in beliefs)
        assert isinstance(beliefs[0], BooleanBelief)
        assert isinstance(beliefs[1], BooleanBelief)
        assert beliefs[1].p < beliefs[0].p

    @pytest.mark.asyncio
    async def test_the_filter_state_is_not_overwritten_by_coupling(self) -> None:
        """結合の事後をフィルタに書き戻さない。

        **書き戻すと同じ関係からのメッセージが周期ごとに複利で積み上がる**（2 周目は
        1 周目の結合済み値を事前としてまた結合する）。フィルタは「自分の証拠だけによる推定」の
        まま保つ。
        """
        worker = BeliefWorker(Settings(), sink=_Sink(), relations=self._relations)
        worker.attach_driver(
            _Driver(
                [
                    [
                        _event_for(LINE, at=T0, p=0.9),
                        _event_for(PUMP, at=T0, p=0.02),
                        _event_for(PUMP_B, at=T0, p=0.02),
                    ]
                ]
            )
        )
        await worker.step()
        own = worker.ingest.filter_for(entity=LINE, fluent="Running").posterior
        assert own is not None
        # フィルタは自分の観測（0.9）由来の値を保っている（結合で下がった値ではない）
        assert own > 0.5

    @pytest.mark.asyncio
    async def test_unchanged_beliefs_are_not_rewritten(self) -> None:
        """結合で動かなかった信念は書かない。

        追記専用なので、変化のない文を毎周期足すと**グラフが単調に膨らみ**、二時制クエリの
        履歴が結合の周期で埋まる。
        """
        sink = _Sink()
        worker = BeliefWorker(Settings(), sink=sink, relations=lambda _priors: ([], []))
        worker.attach_driver(_Driver([[_event(at=T0)]]))
        await worker.step()
        # 関係が無ければ結合由来の文は 1 件も出ない
        assert len(sink.written) == 1

    @pytest.mark.asyncio
    async def test_without_relations_no_coupling_happens(self) -> None:
        """relations が無ければ結合を回さない（周期判定だけが進む）。"""
        worker, _driver, sink = _worker([[_event(at=T0)]])
        await worker.step()
        assert len(sink.written) == 1

    @pytest.mark.asyncio
    async def test_coupling_is_skipped_when_there_are_no_priors(self) -> None:
        """事前が無ければ結合しない（空の priors で propagate を呼ばない）。"""
        worker = BeliefWorker(Settings(), sink=_Sink(), relations=self._relations)
        assert await worker.step() == 0


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


def _situation_relations(
    priors: Mapping[tuple[str, str], float],
) -> tuple[list[PartWhole], list[SituationLink]]:
    """P-101 と P-102 が同じ Situation に参加している（M8-07）。

    部分−全体と違って**向きが無い**ので、対称な引き寄せになる。集約規則も要らない。
    """
    fluent = str(vocab.ent.Running)
    members = tuple(
        entity for entity, name in priors if name == fluent and entity in {PUMP, PUMP_B}
    )
    if len(members) < 2:
        return [], []
    return [], [SituationLink(situation="urn:sbp:sit:line-stop", members=members, fluent=fluent)]


class TestSituationCoupling:
    """同一 Situation による結合（M8-07）。

    重点:

    1. **向きが無い** — 部分−全体と違って対称な引き寄せになる。
    2. **集約規則を要求しない** — 「同じ出来事に参加しているから状態が揃いやすい」という
       弱い制約で、all / any のような向きを持たない。
    3. **部分−全体が無くても結合が回る** — 片方だけでも周期処理が進む。
    """

    @pytest.mark.asyncio
    async def test_situation_members_pull_each_other(self) -> None:
        """同じ Situation の実体どうしで信念が引き寄せられる。

        P-101 は「止まっている」、P-102 は「動いている」と観測されたとき、同じ出来事に
        参加しているなら両者は互いに寄る。
        """
        sink = _Sink()
        worker = BeliefWorker(Settings(), sink=sink, relations=_situation_relations)
        worker.attach_driver(
            _Driver([[_event_for(PUMP, at=T0, p=0.05), _event_for(PUMP_B, at=T0, p=0.95)]])
        )

        await worker.step()

        # 1 件目は前向きフィルタ、2 件目は結合後の調整
        for entity in (PUMP, PUMP_B):
            rows = [row for row in sink.written if row.subject == entity]
            assert len(rows) == 2, f"{entity} の結合後の調整が書かれていない"

        def _coupled(entity: str) -> float:
            belief = [row for row in sink.written if row.subject == entity][1].belief
            assert isinstance(belief, BooleanBelief)
            return belief.p

        # 互いに寄る（低い側は上がり、高い側は下がる）
        assert _coupled(PUMP) > 0.05
        assert _coupled(PUMP_B) < 0.95

    @pytest.mark.asyncio
    async def test_a_lone_member_does_not_couple(self) -> None:
        """参加者が 1 件しか観測されていない Situation は結合にならない。

        ``SituationLink`` は 2 件未満で例外になるので、provider 側で落とす必要がある。
        """
        sink = _Sink()
        worker = BeliefWorker(Settings(), sink=sink, relations=_situation_relations)
        worker.attach_driver(_Driver([[_event_for(PUMP, at=T0, p=0.05)]]))

        # 例外にならず、結合後の書き込みも無い
        await worker.step()

        rows = [row for row in sink.written if row.subject == PUMP]
        assert len(rows) == 1, "結合が成立していないのに調整が書かれている"


class TestMetrics:
    """信念更新の指標（M8-14）。

    重点:

    1. **寄与率は正規化される** — ``SourceContribution`` は対数尤度比の大きさで、
       ``contrib_*`` の単位は ratio（0〜1）。生の値を入れると値域検証で落ちる。
    2. **滞留は負にならない** — 合成データや時計のずれで未来の事象が来ることがある。
    3. **指標が無くても動く** — `metrics=None` で測らないだけ。
    """

    @staticmethod
    def _recorder() -> MetricRecorder:
        return MetricRecorder(site="siteA", sink=None, period_s=0.0)

    @pytest.mark.asyncio
    async def test_an_update_records_latency_and_contributions(self) -> None:
        """信念更新 1 件でレイテンシ・寄与率・エントロピー・滞留が記録される。"""
        recorder = self._recorder()
        worker = BeliefWorker(Settings(), sink=_Sink(), metrics=recorder)
        worker.attach_driver(_Driver([[_event_for(PUMP, at=T0, p=0.9)]]))

        await worker.handle_event(_event_for(PUMP, at=T0, p=0.9))

        # 寄与率 3 件 + レイテンシ + 滞留 + エントロピー
        assert recorder.buffered >= 5

    @pytest.mark.asyncio
    async def test_the_contributions_are_normalised(self) -> None:
        """寄与率が 0〜1 に正規化されている（値域検証を通る）。

        正規化せずに入れると ``MetricSample`` が ``ConfigError`` を投げる。例外が出ないこと
        自体が正規化されている証拠である。
        """
        recorder = self._recorder()
        worker = BeliefWorker(Settings(), sink=_Sink(), metrics=recorder)

        # 例外にならない（生の寄与の大きさは 1 を超えうる）
        await worker.handle_event(_event_for(PUMP, at=T0, p=0.99))

        assert recorder.buffered > 0

    @pytest.mark.asyncio
    async def test_a_future_event_does_not_make_the_lag_negative(self) -> None:
        """未来の事象でも滞留が負にならない（``event_log_lag`` は秒で負を許さない）。"""
        recorder = self._recorder()
        worker = BeliefWorker(Settings(), sink=_Sink(), metrics=recorder)
        future = now() + timedelta(hours=1)

        # 例外にならない（下限 0 にしている）
        await worker.handle_event(_event_for(PUMP, at=future, p=0.9))

        assert recorder.buffered > 0

    @pytest.mark.asyncio
    async def test_without_a_recorder_nothing_is_measured(self) -> None:
        """指標の記録が無くても信念更新は動く。"""
        worker = BeliefWorker(Settings(), sink=_Sink())

        await worker.handle_event(_event_for(PUMP, at=T0, p=0.9))

        assert worker.applied_total == 1
