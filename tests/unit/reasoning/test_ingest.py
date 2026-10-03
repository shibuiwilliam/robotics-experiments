"""イベント → 信念更新の配線のテスト（M7 の運用タスク）。

重点:

1. **実体ごとに別のフィルタが進む** — 1 本に混ぜると別の設備の観測が信念を動かす。
2. **時刻が戻ったイベントを入れない** — 滞在時間が負になり半マルコフが壊れる。
3. **入れなかったことが記録される** — 静かに捨てると、パーティション設定の誤りに気付けない。
4. **監査隔離が効く**（不変条件 6）。
5. **述語 IRI を引けない場合は落ちる**（不変条件 5: 推測で IRI を作らない）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sbp.core import vocab
from sbp.core.audit import AuditMask, no_audit
from sbp.core.belief import BooleanBelief, NumericBelief
from sbp.core.errors import ReasoningError
from sbp.core.events import EventEnvelope, ProbeEvidence, Producer, RecordEvidence, ValidTime
from sbp.core.ids import new_event_id
from sbp.core.modality import Modality
from sbp.reasoning.ingest import (
    BeliefIngest,
    SkipReason,
    default_transition,
    fluent_of_event,
)

T0 = datetime(2026, 6, 10, 9, tzinfo=UTC)
PUMP_A = "https://sbp.example/site/siteA/asset/P-101"
PUMP_B = "https://sbp.example/site/siteA/asset/P-102"


def _ingest() -> BeliefIngest:
    """``Running`` だけを引ける配線（述語は vocab から取る）。"""
    return BeliefIngest(
        provenance="urn:sbp:prov:test",
        ontology_version=vocab.ONTOLOGY_VERSION,
        predicate_of={"Running": str(vocab.ent.Running)},
        transition=default_transition(step=timedelta(seconds=10)),
    )


def _event(
    *,
    entity: str = PUMP_A,
    p: float = 0.95,
    at: datetime = T0,
    fluent: str = "Running",
    numeric: bool = False,
) -> EventEnvelope:
    """プローブ由来の昇格イベント（様相は inferred、不変条件 3）。"""
    belief = NumericBelief(mean=1.0, var=0.1) if numeric else BooleanBelief(p=p)
    return EventEnvelope(
        event_id=new_event_id(),
        type=f"sbp-core:PhysicalEvent/ent:{fluent}",
        subject=entity,
        valid_time=ValidTime(start=at, uncertainty_s=5.0),
        tx_time=at,
        modality=Modality.INFERRED,
        belief=belief,
        evidence=(ProbeEvidence(probe_id="https://sbp.example/probe/Running/1.0.0"),),
        producer=Producer(agent="bridge@siteA", version="0.1.0"),
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


class TestFluentExtraction:
    """イベント型からフルーエントを取り出す。"""

    @pytest.mark.parametrize(
        ("event_type", "expected"),
        [
            ("sbp-core:PhysicalEvent/ent:Running", "Running"),
            ("sbp-core:PhysicalEvent/https://x/ent#Faulted", "Faulted"),
            ("sbp-core:PhysicalEvent/VibrationAnomaly", "VibrationAnomaly"),
        ],
    )
    def test_composite_types_yield_the_local_name(self, event_type: str, expected: str) -> None:
        """複合型の末尾のローカル名を返す。"""
        assert fluent_of_event(event_type) == expected

    @pytest.mark.parametrize(
        "event_type", ["sbp-core:Heartbeat", "ent:Running", "", "sbp-core:PhysicalEvent/"]
    )
    def test_non_composite_types_yield_none(self, event_type: str) -> None:
        """複合型でなければ ``None``。**推測で割り当てない**（不変条件 5 の趣旨）。"""
        assert fluent_of_event(event_type) is None


class TestPerEntityFilters:
    """実体ごとにフィルタが分かれる。"""

    def test_two_entities_do_not_share_state(self) -> None:
        """P-101 の観測が P-102 の信念を動かさない。

        フィルタを 1 本に混ぜると、同じフルーエントを持つ別の設備の観測が混ざる。
        """
        ingest = _ingest()
        for index in range(5):
            at = T0 + timedelta(seconds=10 * index)
            ingest.handle(_event(entity=PUMP_A, p=0.98, at=at))
            ingest.handle(_event(entity=PUMP_B, p=0.02, at=at))

        posterior_a = ingest.filter_for(entity=PUMP_A, fluent="Running").posterior
        posterior_b = ingest.filter_for(entity=PUMP_B, fluent="Running").posterior
        assert posterior_a is not None
        assert posterior_b is not None
        assert posterior_a > 0.9
        assert posterior_b < 0.1

    def test_the_filter_advances_over_the_series(self) -> None:
        """前向きフィルタは 1 回では動ききらない（設計書 §8.1）。

        1 刻みで 0.95 から確信に至らないこと、刻みを重ねると寄っていくことを確かめる。
        """
        ingest = _ingest()
        first = ingest.handle(_event(p=0.95, at=T0))
        assert first.update is not None
        after_many = None
        for index in range(1, 12):
            after_many = ingest.handle(_event(p=0.95, at=T0 + timedelta(seconds=10 * index)))
        assert after_many is not None
        assert after_many.update is not None
        assert after_many.update.posterior > first.update.posterior

    def test_tracked_count_reflects_entity_times_fluent(self) -> None:
        """フィルタの数は実体 × フルーエント。"""
        ingest = _ingest()
        ingest.handle(_event(entity=PUMP_A))
        ingest.handle(_event(entity=PUMP_B))
        ingest.handle(_event(entity=PUMP_A))
        assert ingest.tracked_entities == 2


class TestOrdering:
    """実体内の順序。"""

    def test_an_out_of_order_event_is_refused(self) -> None:
        """時刻が戻ったイベントは入れない（滞在時間が負になる）。"""
        ingest = _ingest()
        ingest.handle(_event(at=T0 + timedelta(minutes=5)))
        late = ingest.handle(_event(at=T0))
        assert not late.applied
        assert late.skipped == SkipReason.OUT_OF_ORDER

    def test_out_of_order_is_scoped_per_entity(self) -> None:
        """別の実体の時刻は互いに影響しない（グローバル順序を仮定しない、不変条件 4）。"""
        ingest = _ingest()
        ingest.handle(_event(entity=PUMP_A, at=T0 + timedelta(minutes=5)))
        other = ingest.handle(_event(entity=PUMP_B, at=T0))
        assert other.applied

    def test_a_batch_is_ordered_per_entity(self) -> None:
        """バッチ内は実体ごとに時刻順へ並べ替えてから入れる。

        トピックのパーティションが実体単位でも、1 バッチには複数実体が混ざる。
        入力順のまま入れると、実体内で時刻が戻って捨てられる件が出る。
        """
        ingest = _ingest()
        batch = [
            _event(entity=PUMP_A, at=T0 + timedelta(seconds=20)),
            _event(entity=PUMP_B, at=T0 + timedelta(seconds=20)),
            _event(entity=PUMP_A, at=T0),
            _event(entity=PUMP_B, at=T0),
        ]
        outcomes = ingest.handle_batch(batch)
        assert all(outcome.applied for outcome in outcomes)
        assert ingest.skipped == {}

    def test_the_same_timestamp_is_accepted(self) -> None:
        """同時刻は「戻った」ではない（右開区間の扱いと同じ考え方）。"""
        ingest = _ingest()
        ingest.handle(_event(at=T0))
        assert ingest.handle(_event(at=T0)).applied


class TestSkipAccounting:
    """入れなかったことを記録する。"""

    def test_non_fluent_events_are_counted(self) -> None:
        """フルーエントを取り出せないイベントは理由つきで数える。"""
        ingest = _ingest()
        outcome = ingest.handle(
            _event(fluent="Running").model_copy(update={"type": "sbp-core:Heartbeat"})
        )
        assert outcome.skipped == SkipReason.NOT_A_FLUENT
        assert ingest.skipped[SkipReason.NOT_A_FLUENT] == 1

    def test_numeric_beliefs_are_counted(self) -> None:
        """真偽以外の信念は別の更新則が必要なので入れない。"""
        ingest = _ingest()
        outcome = ingest.handle(_event(numeric=True))
        assert outcome.skipped == SkipReason.NOT_BOOLEAN

    def test_counts_accumulate_per_reason(self) -> None:
        """理由別に積む（どの経路で落ちているかが分かる）。"""
        ingest = _ingest()
        for _ in range(3):
            ingest.handle(_event(numeric=True))
        assert ingest.skipped[SkipReason.NOT_BOOLEAN] == 3

    def test_a_skipped_event_leaves_no_assertion(self) -> None:
        """入れなかったイベントからアサーションは出ない。"""
        outcome = _ingest().handle(_event(numeric=True))
        assert outcome.assertion is None
        assert outcome.update is None


MES = "https://sbp.example/source/mes"


def _record_event(*, at: datetime = T0, p: float = 0.9) -> EventEnvelope:
    """業務記録由来のイベント（``RecordEvidence`` を持つ）。"""
    return _event(at=at, p=p).model_copy(
        update={
            "evidence": (
                RecordEvidence(
                    record="https://sbp.example/site/siteA/record/r-1",
                    source=MES,
                ),
            ),
            "modality": Modality.OBSERVED,
        }
    )


class TestAuditIsolation:
    """不変条件 6。**プローブ由来と記録由来で挙動が違うのが要点である。**"""

    def test_a_record_event_from_an_audited_source_is_excluded(self) -> None:
        """監査対象の情報源の記録は入力にしない。"""
        ingest = _ingest()
        mask = AuditMask.auditing_source(MES, reason="discrepancy:d-1 の監査")
        outcome = ingest.handle(_record_event(), mask=mask)
        assert outcome.skipped == SkipReason.AUDIT_EXCLUDED

    def test_a_probe_event_is_never_excluded_by_a_record_audit(self) -> None:
        """プローブ由来は記録の監査では除外しない。

        **これが「物理から記録を監査する」ことの中身である。** 物理観測まで除外したら、
        記録を疑うための独立な証拠が無くなり、監査が成立しない。
        """
        ingest = _ingest()
        mask = AuditMask.auditing_source(MES, reason="discrepancy:d-1 の監査")
        assert ingest.handle(_event(), mask=mask).applied

    def test_no_audit_lets_everything_through(self) -> None:
        """空のマスクは「何も監査していない」なので全証拠を使う。"""
        assert _ingest().handle(_event(), mask=no_audit()).applied
        assert _ingest().handle(_record_event(), mask=no_audit()).applied


class TestPredicateResolution:
    """述語 IRI の解決（不変条件 5）。"""

    def test_an_unknown_fluent_raises(self) -> None:
        """述語を引けないフルーエントは落とす。**推測で IRI を作らない。**"""
        ingest = _ingest()
        with pytest.raises(ReasoningError, match="no predicate IRI"):
            ingest.handle(_event(fluent="NotAFluentWeKnow"))

    def test_the_assertion_carries_the_vocab_predicate(self) -> None:
        """アサーションの述語は vocab から来た値である。"""
        outcome = _ingest().handle(_event())
        assert outcome.assertion is not None
        assert outcome.assertion.predicate == str(vocab.ent.Running)

    def test_the_assertion_is_inferred(self) -> None:
        """信念更新の出力は必ず ``inferred``（不変条件 3）。"""
        outcome = _ingest().handle(_event())
        assert outcome.assertion is not None
        assert outcome.assertion.modality is Modality.INFERRED

    def test_provenance_names_the_source_event(self) -> None:
        """来歴から元のイベントが辿れる。"""
        event = _event()
        outcome = _ingest().handle(event)
        assert outcome.assertion is not None
        assert str(event.event_id) in outcome.assertion.provenance


class TestPrune:
    """フィルタの掃除。"""

    def test_stale_filters_are_dropped(self) -> None:
        """古いフィルタを捨てられる（放置すると単調増加する）。"""
        ingest = _ingest()
        ingest.handle(_event(entity=PUMP_A, at=T0))
        ingest.handle(_event(entity=PUMP_B, at=T0 + timedelta(hours=2)))
        dropped = ingest.prune(before=T0 + timedelta(hours=1))
        assert dropped == 1
        assert ingest.tracked_entities == 1

    def test_pruning_does_not_lose_the_belief_itself(self) -> None:
        """捨てたのはフィルタの状態だけで、次の観測は事前から再開する。

        アサーションは知識グラフに残っているので信念は失われない。
        """
        ingest = _ingest()
        ingest.handle(_event(at=T0))
        ingest.prune(before=T0 + timedelta(hours=1))
        resumed = ingest.handle(_event(at=T0 + timedelta(hours=2)))
        assert resumed.applied
        assert resumed.update is not None

    def test_pruning_nothing_returns_zero(self) -> None:
        """捨てるものが無ければ 0。"""
        assert _ingest().prune(before=T0) == 0
