"""ゲート G3 の統合テスト（docs/TASK.md M3-09）。

検収内容: SynthWorld に既知の不一致（幽霊在庫・遅延記帳・状態食い違い）を注入し、
観測 × 記録 × 計画 → 事後 → 不一致起票 までを通して**検出精度 ≥ 0.8 / 再現率 ≥ 0.6** を計測する。

ゲート G3 の条件:

- G2 維持
- M3-09 緑
- ワークドエグザンプル（設計書 §14）の前半（観測 × 記録 × 計画 → 事後 → D2 起票）が
  合成データで再現できる

**なぜ精度と再現率の両方が要るか。** 再現率だけなら「全部起票する」で 1.0 になり、精度だけなら
「何も起票しない」で（分母が 0 になって）測れなくなる。照合エンジンの失敗モードは
「不一致の洪水」（設計書 §15）であり、精度がそれを捕まえる。

真値は ``sbp.testing.injection`` の中だけで参照する（不変条件 10）。検出側は注入の内容を
知らず、プローブ出力と業務記録だけを見る。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pytest

from sbp.bridge import LinearProbe, TemperatureScaler
from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.config import ReasoningSettings
from sbp.core.modality import Modality
from sbp.core.time import Interval, now
from sbp.integration.reliability import ReliabilityRegistry
from sbp.reasoning.reconciliation import (
    BeliefSeries,
    BusinessValueRegistry,
    DetectionContext,
    DiscrepancyKind,
    DiscrepancyStatus,
    RecordedPresence,
    TimedClaim,
    detect_existence,
    detect_state,
    detect_time,
    reconcile,
)
from sbp.testing import (
    LATENT_DIM,
    Fluent,
    InjectionKind,
    SynthConfig,
    SynthWorld,
    inject_discrepancies,
)

if TYPE_CHECKING:
    from sbp.knowledge import AssertionStore
    from sbp.reasoning.reconciliation import Discrepancy
    from sbp.testing import InjectionPlan

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

REPO_ROOT = Path(__file__).resolve().parents[2]
PRECISION_TARGET = 0.8
RECALL_TARGET = 0.6
_LOG_GRANULARITY = timedelta(minutes=30)
"""OperationLog 1 件が主張する時間の長さ（記録の粒度）。"""


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    """4 サイクルの合成世界。

    1 サイクルだと学習側（前半 60%）が稼働一色になり較正の温度が定まらない（片方のクラスしか
    無い）。設計書 §14 の筋書き（起動 → 異常 → 停止）は各サイクルの中で再現される。
    """
    return SynthWorld(
        config=SynthConfig(
            start=now().replace(microsecond=0) - timedelta(hours=4),
            duration=timedelta(hours=4),
            cycles=4,
        ),
        seed=2026,
    )


@pytest.fixture(scope="module")
def plan(world: SynthWorld) -> InjectionPlan:
    return inject_discrepancies(world)


@pytest.fixture(scope="module")
def observed_running(world: SynthWorld) -> np.ndarray:
    """Running プローブの較正済み出力（真値は見ない側の入力）。"""
    latents = world.latents("mid")
    labels = world.oracle.labels_for(Fluent.RUNNING)
    cut = int(len(labels) * 0.6)
    probe = LinearProbe(dim=LATENT_DIM, seed=2026)
    probe.fit(latents[:cut], labels[:cut], epochs=500)
    probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents[:cut]), labels[:cut]))
    return probe.predict(latents).probabilities


@pytest.fixture(scope="module")
def context(world: SynthWorld, plan: InjectionPlan) -> DetectionContext:
    settings = ReasoningSettings()
    return DetectionContext.from_settings(
        settings,
        now=world.timestamps[-1],
        ontology_version=vocab.ONTOLOGY_VERSION,
        values=BusinessValueRegistry.from_directory(
            REPO_ROOT / "value", default_scale=settings.severity_value_scale
        ),
        reliability=ReliabilityRegistry.from_directory(REPO_ROOT / "reliability"),
        entity_types={
            plan.pump: vocab.ent.Pump,
            plan.part: vocab.ent.SparePart,
            plan.clean_entities[0]: vocab.ent.Pump,
            plan.clean_entities[1]: vocab.ent.SparePart,
        },
    )


@pytest.fixture(scope="module")
def detected(
    world: SynthWorld,
    plan: InjectionPlan,
    observed_running: np.ndarray,
    context: DetectionContext,
) -> tuple[Discrepancy, ...]:
    """五類型のうち合成で作れる 3 つを回し、統合・抑制・トリアージまで通す。"""
    reliability = context.reliability
    stamps = world.timestamps

    # 記録由来の確率は信頼性モデルを通す（生の payload の真偽ではない）
    state_records = [
        (iri, record)
        for iri, record in plan.records.items()
        if record.record_type == vocab.ent.OperationLog
    ]
    series: list[BeliefSeries] = []
    evidence: dict[str, tuple[str, ...]] = {}
    for iri, record in state_records:
        model = reliability.for_record(record)
        says_running = bool(record.payload.get("running"))
        assert model.confusion is not None
        implied = (
            model.confusion.true_positive if says_running else 1.0 - model.confusion.true_positive
        )
        entity = record.is_about
        assert entity is not None
        observed = (
            observed_running
            if entity == plan.pump
            # クリーンな実体は記録と一致する観測を持つ（物理も停止している）
            else np.minimum(observed_running, 0.05)
        )
        # 記録の主張には有効範囲がある。1 件の稼働実績が系列全体を主張していることにすると、
        # その記録と無関係な時間帯の食い違いまで同じ記録のせいになる（照合が不当に広がる）。
        # OperationLog は記録の粒度（30 分）だけ先を主張するものとして扱う。
        claim = Interval(
            record.occurred_at or record.recorded_at,
            (record.occurred_at or record.recorded_at) + _LOG_GRANULARITY,
        )
        series.append(
            BeliefSeries(
                entity=entity,
                fluent=vocab.ent.Running,
                timestamps=stamps,
                observed=tuple(float(value) for value in observed),
                recorded=tuple(implied if claim.contains_instant(at) else None for at in stamps),
            )
        )
        evidence.setdefault(entity, ())
        evidence[entity] = (*evidence[entity], iri)

    found = list(detect_state(series, evidence=evidence, context=context))

    # 類型 1: 在庫の存在。注入した側は観測が「無い」、クリーンな側は「有る」
    presence: list[BeliefSeries] = []
    recorded_presence: dict[str, RecordedPresence] = {}
    for iri, record in plan.records.items():
        if record.record_type != vocab.ent.SparePart:
            continue
        entity = record.is_about
        assert entity is not None
        absent = entity == plan.part
        presence.append(
            BeliefSeries(
                entity=entity,
                fluent=vocab.ent.Present,
                timestamps=stamps,
                observed=tuple(0.04 if absent else 0.96 for _ in stamps),
            )
        )
        recorded_presence[entity] = RecordedPresence(
            iri, bool(record.payload.get("present")), record.source, record.record_type
        )
    found.extend(detect_existence(presence, recorded=recorded_presence, context=context))

    # 類型 3: 計上の遅れ。観測時刻は真の停止時刻（物理側の事象昇格が出す時刻の代わり）
    stop_at = world.true_intervals(Fluent.RUNNING)[-1].upper_bound
    claims = [
        TimedClaim(
            subject=record.is_about or "",
            event_type=vocab.ent.StopCmd,
            observed_at=stop_at,
            record=record,
            event_iri=iri,
        )
        for iri, record in state_records
    ]
    found.extend(detect_time(claims, context=context))

    outcome = reconcile(
        found,
        records=plan.records,
        reliability=reliability,
        at=world.timestamps[-1],
    )
    return outcome.triaged


def _score(detected: tuple[Discrepancy, ...], plan: InjectionPlan) -> tuple[float, float, int]:
    """精度・再現率・誤検出数を出す。"""
    matched_injections = set()
    true_positives = 0
    for discrepancy in detected:
        hit = False
        for index, injection in enumerate(plan.injected):
            if any(
                injection.matches(
                    entity=subject, kind=int(discrepancy.kind), interval=discrepancy.interval
                )
                for subject in discrepancy.subjects
            ):
                matched_injections.add(index)
                hit = True
        if hit:
            true_positives += 1
    precision = true_positives / len(detected) if detected else 0.0
    recall = len(matched_injections) / len(plan.injected)
    return precision, recall, len(detected) - true_positives


class TestG3DetectionQuality:
    """ゲート G3 の数値条件（docs/TASK.md M3-09）。"""

    def test_something_was_detected(
        self, detected: tuple[Discrepancy, ...], plan: InjectionPlan
    ) -> None:
        assert detected, "何も検出されなかった。注入: " + ", ".join(
            f"{item.kind.value}@{item.entity}" for item in plan.injected
        )

    def test_recall_meets_the_gate(
        self, detected: tuple[Discrepancy, ...], plan: InjectionPlan
    ) -> None:
        _, recall, _ = _score(detected, plan)
        missed = [
            item.note
            for item in plan.injected
            if not any(
                item.matches(
                    entity=subject, kind=int(discrepancy.kind), interval=discrepancy.interval
                )
                for discrepancy in detected
                for subject in discrepancy.subjects
            )
        ]
        assert recall >= RECALL_TARGET, f"再現率 {recall:.2f} < {RECALL_TARGET}。見逃し: {missed}"

    def test_precision_meets_the_gate(
        self, detected: tuple[Discrepancy, ...], plan: InjectionPlan
    ) -> None:
        precision, _, false_positives = _score(detected, plan)
        assert precision >= PRECISION_TARGET, (
            f"精度 {precision:.2f} < {PRECISION_TARGET}。誤検出 {false_positives} 件: "
            + ", ".join(f"#{int(d.kind)}@{d.subjects[0]}" for d in detected)
        )

    def test_clean_entities_are_not_reported(
        self, detected: tuple[Discrepancy, ...], plan: InjectionPlan
    ) -> None:
        """物理と一致している記録で起票したら、それは洪水の始まりである。"""
        reported = {subject for discrepancy in detected for subject in discrepancy.subjects}
        assert not reported & set(plan.clean_entities), (
            f"クリーンな実体を起票した: {sorted(reported & set(plan.clean_entities))}"
        )

    def test_each_injected_kind_is_representable(self, detected: tuple[Discrepancy, ...]) -> None:
        """三類型それぞれが少なくとも 1 件は検出できる（どれか 1 類型に偏っていない）。"""
        kinds_found = {int(discrepancy.kind) for discrepancy in detected}
        expected = {
            InjectionKind.STATE_MISMATCH.expected_kind,
            InjectionKind.GHOST_INVENTORY.expected_kind,
        }
        assert expected <= kinds_found, f"検出された類型: {sorted(kinds_found)}"


class TestG3WorkedExample:
    """設計書 §14 前半の再現: 観測 × 記録 → 事後 → D2 起票。"""

    def test_the_state_discrepancy_names_both_sides(
        self, detected: tuple[Discrepancy, ...], plan: InjectionPlan
    ) -> None:
        state = [d for d in detected if d.kind is DiscrepancyKind.STATE]
        assert state, "D2（状態不一致）が起票されていない"
        target = next(d for d in state if plan.pump in d.subjects)
        # 原因になった記録が証拠に入っていること（記号側の根拠）
        assert any(iri in target.evidence for iri in plan.records)
        # 対象フルーエントが根拠に入っていること
        assert vocab.ent.Running in target.assertions

    def test_severity_ranks_the_pump_above_the_spare_part(
        self, detected: tuple[Discrepancy, ...], plan: InjectionPlan
    ) -> None:
        """深刻度は業務価値で順位が決まる（ポンプ 845 万 > 予備品 13.2 万）。"""
        by_entity = {d.subjects[0]: d.severity.score for d in detected}
        if plan.pump in by_entity and plan.part in by_entity:
            assert by_entity[plan.pump] > by_entity[plan.part]

    def test_triaged_discrepancies_carry_their_lifecycle(
        self, detected: tuple[Discrepancy, ...]
    ) -> None:
        for discrepancy in detected:
            assert discrepancy.status is DiscrepancyStatus.TRIAGED
            assert discrepancy.history
            assert discrepancy.history[-1].to_status is DiscrepancyStatus.TRIAGED
            assert discrepancy.is_actionable


class TestG3Persistence:
    """不一致から出た信念が二時制ストアに入り、as-of で読める（G0/G1 の維持）。"""

    def test_the_discrepancy_belief_round_trips(
        self,
        detected: tuple[Discrepancy, ...],
        assertion_store: AssertionStore,
        plan: InjectionPlan,
    ) -> None:
        target = next(d for d in detected if plan.pump in d.subjects)
        assertion = Assertion(
            subject=target.subjects[0],
            predicate=vocab.core.concerns,
            object=str(target.discrepancy_id),
            valid_from=target.interval.start,
            valid_to=target.interval.end,
            tx_from=target.detected_at,
            # 照合エンジンの出力は推定である（不変条件 3）
            modality=Modality.INFERRED,
            belief=BooleanBelief(p=min(0.99, max(0.01, target.severity.confidence))),
            provenance=f"discrepancy:{target.discrepancy_id}",
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
        assertion_store.assert_(assertion)

        found = assertion_store.query(
            subject=target.subjects[0],
            predicate=vocab.core.concerns,
            as_of=target.detected_at + timedelta(seconds=1),
            valid_at=target.interval.start + timedelta(seconds=1),
        )
        assert [a.object_ for a in found] == [str(target.discrepancy_id)]
        assert found[0].modality is Modality.INFERRED
        # 確信度は較正済みの値だけが入る（不変条件 7）
        assert 0.0 <= found[0].confidence <= 1.0

    def test_it_is_invisible_before_detection(
        self,
        detected: tuple[Discrepancy, ...],
        assertion_store: AssertionStore,
        plan: InjectionPlan,
    ) -> None:
        """二時制: 検出前の取引時刻では見えない（不変条件 1、G0 の維持）。"""
        target = next(d for d in detected if plan.pump in d.subjects)
        assertion_store.assert_(
            Assertion(
                subject=target.subjects[0],
                predicate=vocab.core.concerns,
                object=str(target.discrepancy_id),
                valid_from=target.interval.start,
                valid_to=target.interval.end,
                tx_from=target.detected_at,
                modality=Modality.INFERRED,
                belief=BooleanBelief(p=0.8),
                provenance=f"discrepancy:{target.discrepancy_id}",
                ontology_version=vocab.ONTOLOGY_VERSION,
            )
        )
        before = assertion_store.query(
            subject=target.subjects[0],
            predicate=vocab.core.concerns,
            as_of=target.detected_at - timedelta(hours=1),
            valid_at=target.interval.start + timedelta(seconds=1),
        )
        assert before == []


class TestG3Interval:
    """区間の扱い（不変条件 8）。"""

    def test_reported_intervals_are_right_open(self, detected: tuple[Discrepancy, ...]) -> None:
        for discrepancy in detected:
            interval = discrepancy.interval
            assert isinstance(interval, Interval)
            assert interval.contains_instant(interval.start)
            assert not interval.contains_instant(interval.upper_bound)
