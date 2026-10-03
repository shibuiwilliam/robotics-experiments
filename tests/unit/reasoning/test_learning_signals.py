"""解決結果 → 学習信号の単体テスト（設計書 §8.2 の表）。

重点:

1. **四つの解決結果が四つの別の処置になる** — 同じ処置に畳まれていたら学習ループは閉じない。
2. **「両方誤り」で信頼性モデルを動かさない** — オントロジー欠落を情報源の品質問題にしない。
3. **写像が無ければ訂正提案を作らない** — どの列を書くか分からないまま業務システムを触らない。
4. **書き戻し提案は必ず補償を持つ** — 取り消せない書き戻しを基盤から出さない（§7.6）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sbp.core import vocab
from sbp.core.records import Record
from sbp.core.time import Interval
from sbp.integration.mappings import MappingRegistry
from sbp.integration.reliability import ReliabilityRegistry
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    DiscrepancyStatus,
    Resolution,
    ResolutionOutcome,
    Severity,
    apply_resolution,
    convert_resolution,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
RECORD_IRI = "record:OL-0912-A"
VERSION = vocab.ONTOLOGY_VERSION


@pytest.fixture(scope="module")
def reliability() -> ReliabilityRegistry:
    return ReliabilityRegistry.from_directory(REPO_ROOT / "reliability")


@pytest.fixture(scope="module")
def mappings() -> MappingRegistry:
    return MappingRegistry.from_directory(REPO_ROOT / "mappings")


@pytest.fixture
def mes_record() -> Record:
    """MES の稼働実績。設計書 §14 で「稼働中」と記録していたもの。"""
    return Record(
        record_type=vocab.ent.OperationLog,
        source=vocab.ent.MesSource,
        external_id="OL-0912-A",
        is_about=PUMP,
        recorded_at=T0 + timedelta(minutes=50),
        occurred_at=T0 + timedelta(minutes=41),
        payload={"running": 1, "shift": "day"},
        ontology_version=VERSION,
    )


@pytest.fixture
def state_discrepancy() -> Discrepancy:
    """設計書 §14 の D2（状態不一致）を Assigned まで進めたもの。"""
    opened = Discrepancy(
        kind=DiscrepancyKind.STATE,
        subjects=(PUMP,),
        assertions=(vocab.ent.Running,),
        evidence=(RECORD_IRI,),
        detected_at=T0 + timedelta(minutes=45),
        interval=Interval(T0 + timedelta(minutes=41), T0 + timedelta(minutes=55)),
        severity=Severity(
            score=0.62, value_at_stake=8_450_000.0, confidence=0.87, persistence_s=840.0
        ),
        detector="kind2",
        ontology_version=VERSION,
    )
    return opened.transition(DiscrepancyStatus.TRIAGED, at=T0).transition(
        DiscrepancyStatus.ASSIGNED,
        at=T0,
        assignee_role="https://sbp.example/role/maintenance-planner",
    )


def _resolution(outcome: ResolutionOutcome) -> Resolution:
    return Resolution(
        outcome=outcome,
        note="現場確認の結果",
        resolved_by="https://sbp.example/person/op-7",
        resolved_at=T0 + timedelta(hours=1),
    )


class TestPhysicalCorrect:
    """物理が正しい → 記録訂正提案 + 信頼性モデル更新。"""

    def test_it_proposes_a_record_correction(
        self,
        state_discrepancy: Discrepancy,
        mes_record: Record,
        reliability: ReliabilityRegistry,
        mappings: MappingRegistry,
    ) -> None:
        artifacts = convert_resolution(
            state_discrepancy,
            resolution=_resolution(ResolutionOutcome.PHYSICAL_CORRECT),
            records={RECORD_IRI: mes_record},
            reliability=reliability,
            mappings=mappings,
            corrections={"running": 0},
            at=T0 + timedelta(hours=1),
        )
        assert len(artifacts.corrections) == 1
        command = artifacts.corrections[0]
        assert command.changes == {"running": 0}
        assert command.target.table == "operation_log"
        assert command.target.external_id == "OL-0912-A"
        # 不変条件に近い要求（設計書 §7.6）: 補償が無い書き戻しは作れない
        assert command.compensation
        assert "不一致" in command.reason

    def test_it_updates_the_source_reliability(
        self,
        state_discrepancy: Discrepancy,
        mes_record: Record,
        reliability: ReliabilityRegistry,
    ) -> None:
        """記録が誤っていたのだから、その情報源の誤記率は上がる方向に動く。"""
        before = reliability.for_record(mes_record)
        artifacts = convert_resolution(
            state_discrepancy,
            resolution=_resolution(ResolutionOutcome.PHYSICAL_CORRECT),
            records={RECORD_IRI: mes_record},
            reliability=reliability,
            at=T0 + timedelta(hours=1),
        )
        assert len(artifacts.reliability_updates) == 1
        after = artifacts.reliability_updates[0]
        assert before.confusion is not None
        assert after.confusion is not None
        assert after.confusion.false_positive > before.confusion.false_positive
        assert after.observations == before.observations + 1
        # レジストリ自身は変わらない（反映は呼び出し側の判断）
        assert reliability.for_record(mes_record).observations == before.observations

    def test_without_a_mapping_no_correction_is_proposed(
        self,
        state_discrepancy: Discrepancy,
        mes_record: Record,
        reliability: ReliabilityRegistry,
    ) -> None:
        artifacts = convert_resolution(
            state_discrepancy,
            resolution=_resolution(ResolutionOutcome.PHYSICAL_CORRECT),
            records={RECORD_IRI: mes_record},
            reliability=reliability,
            corrections={"running": 0},
            at=T0 + timedelta(hours=1),
        )
        assert artifacts.corrections == ()

    def test_an_unknown_field_is_skipped(
        self,
        state_discrepancy: Discrepancy,
        mes_record: Record,
        mappings: MappingRegistry,
    ) -> None:
        """写像に無いキーは書かない（業務システムに知らない列を作らない）。"""
        artifacts = convert_resolution(
            state_discrepancy,
            resolution=_resolution(ResolutionOutcome.PHYSICAL_CORRECT),
            records={RECORD_IRI: mes_record},
            mappings=mappings,
            corrections={"not_a_column": 1},
            at=T0 + timedelta(hours=1),
        )
        assert artifacts.corrections == ()


class TestRecordCorrect:
    """記録が正しい → プローブの負例バッグ。"""

    def test_it_emits_a_negative_bag_over_the_interval(
        self,
        state_discrepancy: Discrepancy,
        mes_record: Record,
        reliability: ReliabilityRegistry,
    ) -> None:
        artifacts = convert_resolution(
            state_discrepancy,
            resolution=_resolution(ResolutionOutcome.RECORD_CORRECT),
            records={RECORD_IRI: mes_record},
            reliability=reliability,
            at=T0 + timedelta(hours=1),
        )
        assert len(artifacts.bags) == 1
        bag = artifacts.bags[0]
        assert bag.positive is False
        assert bag.concept == vocab.ent.Running
        assert bag.entity == PUMP
        assert bag.interval == state_discrepancy.interval
        assert bag.source_record == RECORD_IRI
        assert 0.0 < bag.weight <= 1.0

    def test_the_bag_weight_follows_source_reliability(
        self,
        state_discrepancy: Discrepancy,
        mes_record: Record,
        reliability: ReliabilityRegistry,
    ) -> None:
        """判定が記録に引きずられている可能性があるため、情報源の信頼度を重みに反映する。"""
        artifacts = convert_resolution(
            state_discrepancy,
            resolution=_resolution(ResolutionOutcome.RECORD_CORRECT),
            records={RECORD_IRI: mes_record},
            reliability=reliability,
            at=T0 + timedelta(hours=1),
        )
        model = reliability.for_record(mes_record)
        assert model.confusion is not None
        assert artifacts.bags[0].weight == pytest.approx(model.confusion.true_positive)

    def test_it_also_updates_reliability_upwards(
        self,
        state_discrepancy: Discrepancy,
        mes_record: Record,
        reliability: ReliabilityRegistry,
    ) -> None:
        before = reliability.for_record(mes_record)
        artifacts = convert_resolution(
            state_discrepancy,
            resolution=_resolution(ResolutionOutcome.RECORD_CORRECT),
            records={RECORD_IRI: mes_record},
            reliability=reliability,
            at=T0 + timedelta(hours=1),
        )
        after = artifacts.reliability_updates[0]
        assert before.confusion is not None
        assert after.confusion is not None
        assert after.confusion.true_positive >= before.confusion.true_positive
        assert artifacts.corrections == ()


class TestBothWrong:
    """両方誤り → 概念候補。信頼性モデルは動かさない。"""

    def test_it_proposes_a_concept_candidate(
        self,
        state_discrepancy: Discrepancy,
        mes_record: Record,
        reliability: ReliabilityRegistry,
    ) -> None:
        artifacts = convert_resolution(
            state_discrepancy,
            resolution=_resolution(ResolutionOutcome.BOTH_WRONG),
            records={RECORD_IRI: mes_record},
            reliability=reliability,
            at=T0 + timedelta(hours=1),
        )
        assert len(artifacts.concept_candidates) == 1
        candidate = artifacts.concept_candidates[0]
        assert candidate.entity == PUMP
        assert candidate.interval == state_discrepancy.interval
        assert RECORD_IRI in candidate.evidence

    def test_it_does_not_touch_reliability(
        self,
        state_discrepancy: Discrepancy,
        mes_record: Record,
        reliability: ReliabilityRegistry,
    ) -> None:
        """どちらが悪いか分からないのに誤記率を動かすと、語彙の欠落が隠れる。"""
        artifacts = convert_resolution(
            state_discrepancy,
            resolution=_resolution(ResolutionOutcome.BOTH_WRONG),
            records={RECORD_IRI: mes_record},
            reliability=reliability,
            at=T0 + timedelta(hours=1),
        )
        assert artifacts.reliability_updates == ()
        assert artifacts.bags == ()
        assert artifacts.corrections == ()


class TestUndetermined:
    """判定不能 → 追加観測要求。"""

    def test_it_queues_an_observation_request(self, state_discrepancy: Discrepancy) -> None:
        artifacts = convert_resolution(
            state_discrepancy,
            resolution=_resolution(ResolutionOutcome.UNDETERMINED),
            at=T0 + timedelta(hours=1),
        )
        assert len(artifacts.observation_requests) == 1
        request = artifacts.observation_requests[0]
        assert request.entity == PUMP
        assert request.fluent == vocab.ent.Running
        # 効用のヒントは深刻度（スケジューラが EIG と掛ける）
        assert request.utility == pytest.approx(state_discrepancy.severity.score)

    def test_it_produces_no_labels(self, state_discrepancy: Discrepancy) -> None:
        """判定できていないのに学習ラベルを出さない（誤ったラベルは害になる）。"""
        artifacts = convert_resolution(
            state_discrepancy,
            resolution=_resolution(ResolutionOutcome.UNDETERMINED),
            at=T0 + timedelta(hours=1),
        )
        assert artifacts.bags == ()
        assert artifacts.signals.labels_emitted == ()


class TestApplyResolution:
    """解決からライフサイクル終端までを 1 手で進める経路。"""

    def test_it_reaches_learned_with_signals(
        self,
        state_discrepancy: Discrepancy,
        mes_record: Record,
        reliability: ReliabilityRegistry,
    ) -> None:
        learned, artifacts = apply_resolution(
            state_discrepancy,
            resolution=_resolution(ResolutionOutcome.PHYSICAL_CORRECT),
            records={RECORD_IRI: mes_record},
            reliability=reliability,
            at=T0 + timedelta(hours=1),
        )
        assert learned.status is DiscrepancyStatus.LEARNED
        assert learned.learning is not None
        assert learned.learning.reliability_updates == artifacts.signals.reliability_updates
        assert [change.to_status for change in learned.history][-2:] == [
            DiscrepancyStatus.RESOLVED,
            DiscrepancyStatus.LEARNED,
        ]

    def test_a_resolution_that_teaches_nothing_stays_resolved(
        self, state_discrepancy: Discrepancy
    ) -> None:
        """何も学べなかった解決を ``Learned`` にすると、学習済みの件数が嘘になる。"""
        bare = state_discrepancy.model_copy(update={"assertions": (), "evidence": ()})
        resolved, artifacts = apply_resolution(
            bare,
            resolution=_resolution(ResolutionOutcome.RECORD_CORRECT),
            at=T0 + timedelta(hours=1),
        )
        assert artifacts.is_empty
        assert resolved.status is DiscrepancyStatus.RESOLVED
