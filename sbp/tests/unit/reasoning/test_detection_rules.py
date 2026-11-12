"""五類型の検出規則の単体テスト（設計書 §8.2）。

重点:

1. **τ を満たさないちらつきは起票しない** — これが無いと照合エンジンはノイズ発生器になる。
2. **閾値は情報源ごとの信頼性モデルから導く** — 類型 3 で MES と手書き日報を同じ閾値で見ない。
3. **沈黙と否定を区別する** — 類型 1 で「記録が無い」と「記録が無いと言っている」を混ぜない。
4. **規範は信念の上で、発効期間内でのみ判定する** — 失効した規程で起票しない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from sbp.bridge.identity import EvidenceKind, IdentityClaim
from sbp.core import vocab
from sbp.core.config import ReasoningSettings
from sbp.core.errors import ReasoningError
from sbp.core.records import Norm, NormSeverity, Record
from sbp.core.time import Interval
from sbp.integration.reliability import ReliabilityRegistry
from sbp.reasoning.reconciliation import (
    BeliefSeries,
    BusinessValueRegistry,
    DetectionContext,
    DiscrepancyKind,
    NormViolation,
    RecordedPresence,
    SiteOverride,
    TimedClaim,
    detect_existence,
    detect_identity,
    detect_norm,
    detect_state,
    detect_time,
    jensen_shannon_distance,
    sustained_runs,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
STEP = timedelta(seconds=10)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
PART = "https://sbp.example/site/siteA/part/SP-77"
VERSION = vocab.ONTOLOGY_VERSION


@pytest.fixture(scope="module")
def settings() -> ReasoningSettings:
    return ReasoningSettings()


@pytest.fixture(scope="module")
def context(settings: ReasoningSettings) -> DetectionContext:
    return DetectionContext.from_settings(
        settings,
        now=T0 + timedelta(hours=2),
        ontology_version=VERSION,
        values=BusinessValueRegistry.from_directory(
            REPO_ROOT / "value", default_scale=settings.severity_value_scale
        ),
        reliability=ReliabilityRegistry.from_directory(REPO_ROOT / "reliability"),
        entity_types={PUMP: vocab.ent.Pump, PART: vocab.ent.SparePart},
    )


def _stamps(count: int, *, start: datetime = T0) -> tuple[datetime, ...]:
    return tuple(start + index * STEP for index in range(count))


class TestPersistence:
    """τ（持続時間）の扱い。類型 1・2・5 の共通土台。"""

    def test_a_flicker_shorter_than_tau_is_not_a_run(self) -> None:
        stamps = _stamps(60)  # 10 分
        flags = [index in range(10, 15) for index in range(60)]  # 50 秒だけ成立
        assert sustained_runs(stamps, flags, persistence=timedelta(minutes=5)) == []

    def test_a_run_of_exactly_tau_counts(self) -> None:
        stamps = _stamps(60)
        flags = [index in range(0, 30) for index in range(60)]  # 300 秒 = τ
        runs = sustained_runs(stamps, flags, persistence=timedelta(minutes=5))
        assert len(runs) == 1
        assert runs[0].duration == timedelta(minutes=5)

    def test_runs_are_right_open(self) -> None:
        """不変条件 8: 区間は右開 ``[start, end)``。"""
        stamps = _stamps(60)
        flags = [index < 40 for index in range(60)]
        run = sustained_runs(stamps, flags, persistence=timedelta(minutes=1))[0]
        assert run.contains_instant(run.start)
        assert not run.contains_instant(run.upper_bound)

    def test_unsorted_timestamps_are_rejected(self) -> None:
        stamps = (T0, T0 + STEP, T0)
        with pytest.raises(ReasoningError, match="strictly increasing"):
            sustained_runs(stamps, [True, True, True], persistence=STEP)

    def test_length_mismatch_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="but flags has"):
            sustained_runs(_stamps(3), [True], persistence=STEP)


class TestJensenShannon:
    """類型 2 の乖離尺度。"""

    def test_identical_distributions_have_zero_distance(self) -> None:
        assert jensen_shannon_distance(0.3, 0.3) == pytest.approx(0.0, abs=1e-9)

    def test_it_is_symmetric(self) -> None:
        assert jensen_shannon_distance(0.1, 0.8) == pytest.approx(jensen_shannon_distance(0.8, 0.1))

    def test_confident_disagreement_beats_uncertain_disagreement(self) -> None:
        """同じ差 0.1 でも「確信した否定」の方が大きく出る（距離を使う理由）。"""
        uncertain = jensen_shannon_distance(0.50, 0.60)
        confident = jensen_shannon_distance(0.90, 1.00)
        assert confident > uncertain

    def test_out_of_range_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="must be a probability"):
            jensen_shannon_distance(1.5, 0.5)


class TestKind1Existence:
    """類型 1: 存在不一致。(a) 物理にあるのに記録に無い / (b) 記録にあるのに物理に無い。"""

    def _present(self, probability: float, count: int = 60) -> BeliefSeries:
        return BeliefSeries(
            entity=PART,
            fluent=vocab.ent.Present,
            timestamps=_stamps(count),
            observed=tuple(probability for _ in range(count)),
        )

    def test_ghost_inventory_is_detected(self, context: DetectionContext) -> None:
        """記録は「ある」、物理観測は「無い」——幽霊在庫。"""
        found = detect_existence(
            [self._present(0.05)],
            recorded={
                PART: RecordedPresence(
                    "record:GR-11", True, vocab.ent.ErpSource, vocab.ent.SparePart
                )
            },
            context=context,
        )
        assert len(found) == 1
        assert found[0].kind is DiscrepancyKind.EXISTENCE
        assert found[0].detector.endswith("recorded_present_absent")
        assert found[0].severity.confidence == pytest.approx(0.95)

    def test_unrecorded_stock_is_detected_when_records_are_expected(
        self, context: DetectionContext
    ) -> None:
        """物理にあるのに記録が沈黙している——記録があるはずの実体に限って起票する。"""
        found = detect_existence(
            [self._present(0.97)],
            recorded={},
            expect_records_for=frozenset({PART}),
            context=context,
        )
        assert len(found) == 1
        assert found[0].detector.endswith("physical_present_unrecorded")

    def test_silence_alone_does_not_fire(self, context: DetectionContext) -> None:
        """記録する習慣が無い実体の沈黙を不一致にしない（沈黙は主張ではない）。"""
        assert detect_existence([self._present(0.97)], recorded={}, context=context) == []

    def test_a_record_saying_absent_does_fire(self, context: DetectionContext) -> None:
        """記録が『無い』と主張しているなら、沈黙と違って起票する。"""
        found = detect_existence(
            [self._present(0.97)],
            recorded={
                PART: RecordedPresence(
                    "record:GI-3", False, vocab.ent.ErpSource, vocab.ent.SparePart
                )
            },
            context=context,
        )
        assert len(found) == 1
        assert found[0].detector.endswith("physical_present_unrecorded")

    def test_agreement_does_not_fire(self, context: DetectionContext) -> None:
        found = detect_existence(
            [self._present(0.97)],
            recorded={
                PART: RecordedPresence(
                    "record:GR-11", True, vocab.ent.ErpSource, vocab.ent.SparePart
                )
            },
            context=context,
        )
        assert found == []

    def test_a_brief_absence_does_not_fire(self, context: DetectionContext) -> None:
        """τ 未満の不在（遮蔽・一時的な持ち出し）で幽霊在庫を起票しない。"""
        count = 60
        series = BeliefSeries(
            entity=PART,
            fluent=vocab.ent.Present,
            timestamps=_stamps(count),
            observed=tuple(0.05 if index in range(10, 20) else 0.95 for index in range(count)),
        )
        found = detect_existence(
            [series],
            recorded={
                PART: RecordedPresence(
                    "record:GR-11", True, vocab.ent.ErpSource, vocab.ent.SparePart
                )
            },
            context=context,
        )
        assert found == []


class TestKind2State:
    """類型 2: 状態不一致（設計書 §14 のワークドエグザンプル）。"""

    def _series(self, observed: float, recorded: float | None, count: int = 90) -> BeliefSeries:
        return BeliefSeries(
            entity=PUMP,
            fluent=vocab.ent.Running,
            timestamps=_stamps(count),
            observed=tuple(observed for _ in range(count)),
            recorded=tuple(recorded for _ in range(count)),
        )

    def test_the_worked_example_fires(self, context: DetectionContext) -> None:
        """MES は稼働（信頼性モデル越しに 0.75）、観測は停止（0.13）。"""
        found = detect_state(
            [self._series(0.13, 0.75)],
            evidence={PUMP: ("record:OL-0912-A",)},
            context=context,
        )
        assert len(found) == 1
        assert found[0].kind is DiscrepancyKind.STATE
        assert found[0].evidence == ("record:OL-0912-A",)
        assert found[0].severity.confidence >= context.theta_state_js

    def test_agreement_does_not_fire(self, context: DetectionContext) -> None:
        assert detect_state([self._series(0.92, 0.75)], context=context) == []

    def test_mutual_uncertainty_does_not_fire(self, context: DetectionContext) -> None:
        """どちらも分かっていない（0.45 対 0.55）ときに起票しない。"""
        assert detect_state([self._series(0.45, 0.55)], context=context) == []

    def test_without_records_there_is_nothing_to_compare(self, context: DetectionContext) -> None:
        assert detect_state([self._series(0.05, None)], context=context) == []

    def test_site_override_tightens_the_threshold(self, context: DetectionContext) -> None:
        """拠点別上書き（設計書 §8.2）。記録が雑な拠点では θ を上げて洪水を防ぐ。"""
        borderline = self._series(0.13, 0.75)
        assert detect_state([borderline], context=context) != []
        strict = context.for_site(SiteOverride(site="siteB", theta_state_js=0.9))
        assert detect_state([borderline], context=strict) == []


class TestKind3Time:
    """類型 3: 時間不一致。閾値は情報源ごとの許容プロファイルから導く。"""

    def _record(
        self,
        *,
        source: str,
        record_type: str,
        occurred_at: datetime,
        recorded_at: datetime | None = None,
    ) -> Record:
        return Record(
            record_type=record_type,
            source=source,
            external_id="X-1",
            is_about=PUMP,
            recorded_at=recorded_at or occurred_at,
            occurred_at=occurred_at,
            ontology_version=VERSION,
        )

    def test_a_small_lag_on_an_automated_source_fires(self, context: DetectionContext) -> None:
        """MES は ±5 分が許容。30 分ずれたら異常。"""
        claim = TimedClaim(
            subject=PUMP,
            event_type=vocab.ent.StopCmd,
            observed_at=T0,
            record=self._record(
                source=vocab.ent.MesSource,
                record_type=vocab.ent.OperationLog,
                occurred_at=T0 + timedelta(minutes=30),
            ),
            event_iri="event:stop-1",
        )
        found = detect_time([claim], context=context)
        assert len(found) == 1
        assert found[0].kind is DiscrepancyKind.TIME
        assert found[0].detector.endswith("offset")

    def test_the_same_lag_on_a_batch_source_does_not_fire(self, context: DetectionContext) -> None:
        """CMMS の作業指示は平均 12 時間遅れが正常。30 分は許容内。"""
        claim = TimedClaim(
            subject=PUMP,
            event_type=vocab.ent.MaintEnd,
            observed_at=T0,
            record=self._record(
                source=vocab.ent.CmmsSource,
                record_type=vocab.ent.WorkOrder,
                occurred_at=T0 + timedelta(minutes=30),
            ),
        )
        assert detect_time([claim], context=context) == []

    def test_order_violations_are_detected_independently_of_magnitude(
        self, context: DetectionContext
    ) -> None:
        """物理では停止→保全開始、記録では保全開始→停止。ずれが小さくても矛盾。"""
        stop = TimedClaim(
            subject=PUMP,
            event_type=vocab.ent.StopCmd,
            observed_at=T0,
            record=self._record(
                source=vocab.ent.MesSource,
                record_type=vocab.ent.OperationLog,
                occurred_at=T0 + timedelta(minutes=4),
            ),
        )
        maint = TimedClaim(
            subject=PUMP,
            event_type=vocab.ent.MaintStart,
            observed_at=T0 + timedelta(minutes=2),
            record=self._record(
                source=vocab.ent.MesSource,
                record_type=vocab.ent.OperationLog,
                occurred_at=T0 + timedelta(minutes=1),
            ),
        )
        found = detect_time([stop, maint], context=context)
        assert [d.detector for d in found] == ["kind3:order"]
        assert set(found[0].assertions) == {vocab.ent.StopCmd, vocab.ent.MaintStart}

    def test_consistent_order_does_not_fire(self, context: DetectionContext) -> None:
        first = TimedClaim(
            subject=PUMP,
            event_type=vocab.ent.StopCmd,
            observed_at=T0,
            record=self._record(
                source=vocab.ent.MesSource,
                record_type=vocab.ent.OperationLog,
                occurred_at=T0 + timedelta(minutes=1),
            ),
        )
        second = TimedClaim(
            subject=PUMP,
            event_type=vocab.ent.MaintStart,
            observed_at=T0 + timedelta(minutes=5),
            record=self._record(
                source=vocab.ent.MesSource,
                record_type=vocab.ent.OperationLog,
                occurred_at=T0 + timedelta(minutes=6),
            ),
        )
        assert detect_time([first, second], context=context) == []

    def test_recorded_at_stands_in_when_occurred_at_is_missing(
        self, context: DetectionContext
    ) -> None:
        """発生時刻を持たない記録は計上時刻で判定する（それしか無いので）。"""
        record = Record(
            record_type=vocab.ent.OperationLog,
            source=vocab.ent.MesSource,
            external_id="X-2",
            is_about=PUMP,
            recorded_at=T0 + timedelta(hours=1),
            ontology_version=VERSION,
        )
        claim = TimedClaim(
            subject=PUMP, event_type=vocab.ent.StopCmd, observed_at=T0, record=record
        )
        assert claim.offset == timedelta(hours=1)
        assert len(detect_time([claim], context=context)) == 1


class TestKind4Identity:
    """類型 4: 同一性不一致。他のすべての不一致を汚染するため独立の類型。"""

    def _claim(
        self,
        *,
        entity: str = PUMP,
        track_id: UUID | None = None,
        confidence: float = 0.9,
        status: str = "active",
    ) -> IdentityClaim:
        return IdentityClaim(
            track_id=track_id or uuid4(),
            entity=entity,
            evidence=((EvidenceKind.SPATIOTEMPORAL, 0.9),),
            confidence=confidence,
            valid_from=T0,
            valid_to=T0 + timedelta(hours=1),
            status=status,
            revocation_reason="確信度が閾値を下回った" if status == "revoked" else None,
        )

    def test_a_revoked_claim_is_reported(self, context: DetectionContext) -> None:
        found = detect_identity([self._claim(status="revoked", confidence=0.3)], context=context)
        assert len(found) == 1
        assert found[0].detector.endswith("revoked")
        assert found[0].severity.confidence == pytest.approx(0.7)

    def test_one_track_bound_to_two_entities_conflicts(self, context: DetectionContext) -> None:
        track = uuid4()
        found = detect_identity(
            [
                self._claim(entity=PUMP, track_id=track),
                self._claim(entity=PART, track_id=track),
            ],
            context=context,
        )
        detectors = [d.detector for d in found]
        assert "kind4:track_bound_to_many_entities" in detectors
        conflict = next(d for d in found if d.detector.endswith("many_entities"))
        assert set(conflict.subjects) == {PUMP, PART}

    def test_one_entity_bound_to_two_tracks_conflicts(self, context: DetectionContext) -> None:
        found = detect_identity(
            [self._claim(track_id=uuid4()), self._claim(track_id=uuid4())], context=context
        )
        assert [d.detector for d in found] == ["kind4:entity_bound_to_many_tracks"]

    def test_consistent_claims_do_not_fire(self, context: DetectionContext) -> None:
        track = uuid4()
        assert detect_identity([self._claim(track_id=track)], context=context) == []


class TestKind5Norm:
    """類型 5: 規範不一致。信念の上で、発効期間内でのみ判定する。"""

    def _norm(
        self,
        *,
        severity: NormSeverity = NormSeverity.IMPORTANT,
        effective_from: datetime = T0 - timedelta(days=365),
        effective_to: datetime | None = None,
    ) -> Norm:
        return Norm(
            norm_type=vocab.ent.MaintenancePlan,
            rule_ref="inspection_interval",
            source_document="https://sbp.example/doc/maintenance-standard",
            source_clause="4.2",
            severity=severity,
            applies_to_type=vocab.ent.Pump,
            effective_from=effective_from,
            effective_to=effective_to,
            ontology_version=VERSION,
        )

    def _violation(self, confidence: float, norm: Norm | None = None) -> NormViolation:
        return NormViolation(
            norm=norm or self._norm(),
            subject=PUMP,
            interval=Interval(T0, T0 + timedelta(hours=1)),
            confidence=confidence,
            assertions=(vocab.ent.Inspected,),
            evidence=("record:WO-9",),
        )

    def test_a_confident_violation_is_reported(self, context: DetectionContext) -> None:
        found = detect_norm([self._violation(0.85)], context=context)
        assert len(found) == 1
        assert found[0].kind is DiscrepancyKind.NORM
        assert found[0].severity.norm_weight == pytest.approx(NormSeverity.IMPORTANT.weight)
        # 根拠文書が証拠に入る（監査性要件）
        assert "https://sbp.example/doc/maintenance-standard" in found[0].evidence

    def test_an_uncertain_violation_is_not_reported(self, context: DetectionContext) -> None:
        """θ_5 未満の信念で規程違反を起票しない（人の評価に影響するため）。"""
        assert detect_norm([self._violation(0.5)], context=context) == []

    def test_an_expired_norm_does_not_fire(self, context: DetectionContext) -> None:
        expired = self._norm(
            effective_from=T0 - timedelta(days=365), effective_to=T0 - timedelta(days=1)
        )
        assert detect_norm([self._violation(0.95, expired)], context=context) == []

    def test_a_critical_norm_scores_higher_than_an_advisory_one(
        self, context: DetectionContext
    ) -> None:
        critical = detect_norm(
            [self._violation(0.9, self._norm(severity=NormSeverity.CRITICAL))], context=context
        )[0]
        advisory = detect_norm(
            [self._violation(0.9, self._norm(severity=NormSeverity.ADVISORY))], context=context
        )[0]
        assert critical.severity.score > advisory.severity.score
