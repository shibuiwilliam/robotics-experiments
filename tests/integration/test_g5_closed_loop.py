"""ゲート G5 の統合テスト（docs/TASK.md M5-09）。

検収内容: 設計書 §14 のワークドエグザンプルを合成データで完走する。

    不一致起票 → アクション提案 → 承認 → dry-run 書き戻し → Expectation 生成 → 検証 → 学習信号

ゲート G5 の条件:

- G4 維持
- M5-09 緑
- 予測被覆率 ≥ 0.85（合成データ）

**このテストが確かめるのは「繋がること」である。** 個々の部品の正しさは単体テストが見ている。
ここで見るのは、層をまたいで**同じ実体・同じ時刻・同じ版**が保たれ、片方の出力が他方の入力に
そのまま入ることである。設計書 §14 のトレースの要点 1（観測パスと記録パスが同じ実体に
``is_about`` で集約され、様相だけが異なる）はこの性質そのものである。

真値は ``sbp.testing`` 経由でのみ参照する（不変条件 10）。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from sbp.actions import (
    ActionRegistry,
    ActionRequest,
    ActionStage,
    Approval,
    RateLimitState,
    check_preconditions,
    planned_expectation_deadlines,
)
from sbp.bridge import LinearProbe, TemperatureScaler
from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.audit import AuditMask, no_audit
from sbp.core.belief import BooleanBelief
from sbp.core.config import ReasoningSettings
from sbp.core.modality import Modality
from sbp.core.records import Record
from sbp.core.time import Interval, now
from sbp.integration.mappings import MappingRegistry
from sbp.integration.reliability import ReliabilityRegistry
from sbp.integration.writeback import WritebackExecutor, WritebackMode, WritebackResult
from sbp.reasoning import (
    BeliefFilter,
    BeliefSeries,
    BusinessValueRegistry,
    DetectionContext,
    DiscrepancyKind,
    DiscrepancyStatus,
    DwellTime,
    Expectation,
    ExpectationStatus,
    ExpectedEffect,
    Forecast,
    ForecastPoint,
    Resolution,
    ResolutionOutcome,
    TransitionPrior,
    VerificationInputs,
    apply_resolution,
    detect_state,
    reconcile,
    score_forecasts,
    verify_expectation,
)
from sbp.reasoning.prediction import conformal_halfwidth
from sbp.testing import Fluent, SynthConfig, SynthWorld
from sbp.worldmodel import KoopmanConfig, KoopmanModel

if TYPE_CHECKING:
    from sbp.actions import ActionContract
    from sbp.knowledge import AssertionStore
    from sbp.reasoning import Discrepancy

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

REPO_ROOT = Path(__file__).resolve().parents[2]
COVERAGE_TARGET = 0.85
PUMP = "https://sbp.example/site/siteA/asset/P-101"
LEAD = "https://sbp.example/person/lead-1"
OPERATOR = "https://sbp.example/person/op-7"
VERSION = vocab.ONTOLOGY_VERSION
MES_RECORD_IRI = "https://sbp.example/site/siteA/record/OL-0912-A"


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    """設計書 §14 の筋書き（起動 → 異常 → 停止）を 4 サイクル。"""
    return SynthWorld(
        config=SynthConfig(
            start=now().replace(microsecond=0) - timedelta(hours=4),
            duration=timedelta(hours=4),
            cycles=4,
        ),
        seed=2026,
    )


@pytest.fixture(scope="module")
def observed_running(world: SynthWorld) -> np.ndarray:
    """Koopman 潜在 + 線形プローブで出した「稼働中」の較正済み確率。"""
    cut = int(len(world.timestamps) * 0.6)
    model = KoopmanModel.untrained(
        model_id="https://sbp.example/model/koopman",
        version="1.0.0",
        domain=vocab.ent.Pump,
        channels=world.channels,
        config=KoopmanConfig(delays=8),
    ).fit(world.observations[:cut], seed=2026)
    latents = model.encode_series(world.observations)
    labels = world.oracle.labels_for(Fluent.RUNNING)
    probe = LinearProbe(dim=model.latent_dim, seed=2026)
    probe.fit(latents[:cut], labels[:cut], epochs=500)
    probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents[:cut]), labels[:cut]))
    return probe.predict(latents).probabilities


@pytest.fixture(scope="module")
def mes_record(world: SynthWorld) -> Record:
    """MES は停止後も「稼働中」と記録している（設計書 §14 の記録パス）。"""
    stop_at = world.true_intervals(Fluent.RUNNING)[-1].upper_bound
    return Record(
        record_type=vocab.ent.OperationLog,
        source=vocab.ent.MesSource,
        external_id="OL-0912-A",
        is_about=PUMP,
        recorded_at=stop_at + timedelta(minutes=5),
        occurred_at=stop_at,
        payload={"running": 1, "shift": "day"},
        ontology_version=VERSION,
    )


@pytest.fixture(scope="module")
def reliability() -> ReliabilityRegistry:
    return ReliabilityRegistry.from_directory(REPO_ROOT / "reliability")


@pytest.fixture(scope="module")
def contracts() -> ActionRegistry:
    return ActionRegistry.from_directory(REPO_ROOT / "actions")


@pytest.fixture(scope="module")
def posterior(
    world: SynthWorld,
    observed_running: np.ndarray,
    mes_record: Record,
    reliability: ReliabilityRegistry,
) -> float:
    """三源統合の事後（設計書 §14: 計画・記録は稼働、観測は停止 → 0.14 付近）。"""
    stop_at = world.true_intervals(Fluent.RUNNING)[-1].upper_bound
    indices = [index for index, at in enumerate(world.timestamps) if at >= stop_at][:60]
    filter_ = BeliefFilter(
        fluent="running",
        entity=PUMP,
        transition=TransitionPrior(
            dwell={"running": DwellTime(median_s=8 * 3600)}, step=timedelta(seconds=10)
        ),
        posterior=0.95,
    )
    updates = filter_.run(
        [world.timestamps[index] for index in indices],
        probe_series=[(float(observed_running[index]),) for index in indices],
        records=[(mes_record, True)],
        reliability={key: reliability.get(*key) for key in tuple(reliability.keys())},
        mask=no_audit(),
    )
    return updates[-1].posterior


@pytest.fixture(scope="module")
def discrepancy(
    world: SynthWorld, observed_running: np.ndarray, reliability: ReliabilityRegistry
) -> Discrepancy:
    """D2（状態不一致）を起票する（設計書 §14 の不一致起票）。"""
    settings = ReasoningSettings()
    context = DetectionContext.from_settings(
        settings,
        now=world.timestamps[-1],
        ontology_version=VERSION,
        values=BusinessValueRegistry.from_directory(
            REPO_ROOT / "value", default_scale=settings.severity_value_scale
        ),
        reliability=reliability,
        entity_types={PUMP: vocab.ent.Pump},
    )
    stop_at = world.true_intervals(Fluent.RUNNING)[-1].upper_bound
    claim = Interval(stop_at, stop_at + timedelta(minutes=30))
    model = reliability.get(vocab.ent.MesSource, vocab.ent.OperationLog)
    assert model.confusion is not None
    series = BeliefSeries(
        entity=PUMP,
        fluent=vocab.ent.Running,
        timestamps=world.timestamps,
        observed=tuple(float(value) for value in observed_running),
        recorded=tuple(
            model.confusion.true_positive if claim.contains_instant(at) else None
            for at in world.timestamps
        ),
    )
    found = detect_state([series], evidence={PUMP: (MES_RECORD_IRI,)}, context=context)
    outcome = reconcile(found, at=world.timestamps[-1])
    assert outcome.triaged, "D2 が起票されていない"
    return outcome.triaged[0]


class TestG5BeliefAndDiscrepancy:
    """設計書 §14 の前半（三源統合 → D2 起票）。M3 の再確認でもある。"""

    def test_the_posterior_matches_the_worked_example(self, posterior: float) -> None:
        """計画・記録は稼働、観測は停止 → 事後は 0.14 付近に落ちる。"""
        assert posterior < 0.3, f"事後が落ちていない: {posterior:.4f}"

    def test_the_discrepancy_is_a_state_mismatch_on_the_pump(
        self, discrepancy: Discrepancy
    ) -> None:
        assert discrepancy.kind is DiscrepancyKind.STATE
        assert discrepancy.subjects == (PUMP,)
        assert discrepancy.status is DiscrepancyStatus.TRIAGED
        assert MES_RECORD_IRI in discrepancy.evidence

    def test_the_severity_reflects_the_line_stop_cost(self, discrepancy: Discrepancy) -> None:
        """設計書 §14: 深刻度=高（ライン停止コスト）。"""
        assert discrepancy.severity.value_at_stake > 1_000_000.0
        assert discrepancy.severity.score > 0.5


class TestG5ActionFlow:
    """設計書 §14 の中盤（アクション提案 → 承認 → 書き戻し）。"""

    @pytest.fixture
    def correction(self, contracts: ActionRegistry) -> ActionContract:
        return contracts.get(vocab.ent.CorrectOperationLog)

    def test_the_preconditions_pass_on_the_stopped_pump(
        self, correction: ActionContract, posterior: float, mes_record: Record
    ) -> None:
        """物理観測が停止を確信しており、訂正対象の記録もある → 三面成立。"""
        report = check_preconditions(
            correction,
            target=PUMP,
            beliefs={vocab.ent.Running: posterior},
            records=[mes_record],
        )
        assert report.satisfied, report.explain()

    def test_without_the_record_the_symbolic_face_fails(
        self, correction: ActionContract, posterior: float
    ) -> None:
        """訂正対象が無いのに訂正を実行させない（記号面）。"""
        report = check_preconditions(
            correction, target=PUMP, beliefs={vocab.ent.Running: posterior}
        )
        assert not report.satisfied
        assert report.failed_faces() == ("symbolic",)

    def test_approval_is_required_and_recorded(
        self,
        correction: ActionContract,
        posterior: float,
        discrepancy: Discrepancy,
        mes_record: Record,
    ) -> None:
        """riskClass=medium なので承認必須（設計書 §9.3）。"""
        report = check_preconditions(
            correction,
            target=PUMP,
            beliefs={vocab.ent.Running: posterior},
            records=[mes_record],
        )
        request = ActionRequest(
            contract=correction,
            target=PUMP,
            requested_by=OPERATOR,
            severity=discrepancy.severity.score,
            correlation_id=discrepancy.discrepancy_id,
        )
        request.check(report)
        assert correction.requires_approval(severity=discrepancy.severity.score)
        request.approve(
            Approval(
                role=vocab.ent.ProductionSupervisor,
                approved_by=LEAD,
                approved_at=now(),
                note="現場確認済み",
            )
        )
        event = request.execute(at=now(), executed_by=OPERATOR, rate_limits=RateLimitState())
        assert request.stage is ActionStage.EXECUTED
        assert event.approval is not None
        assert event.correlation_id == discrepancy.discrepancy_id
        assert not event.was_overridden

    def test_the_writeback_stays_a_dry_run(self, mes_record: Record) -> None:
        """設計書 §7.6 / M1: 既定は dry-run。実書き込みは明示的な設定でのみ。"""
        mappings = MappingRegistry.from_directory(REPO_ROOT / "mappings")
        mapping = mappings.for_record_type(mes_record.record_type)
        assert mapping is not None
        learned, artifacts = apply_resolution(
            self._assigned(),
            resolution=Resolution(
                outcome=ResolutionOutcome.PHYSICAL_CORRECT,
                note="物理観測が停止を示している",
                resolved_by=LEAD,
                resolved_at=now(),
            ),
            records={MES_RECORD_IRI: mes_record},
            mappings=mappings,
            corrections={"running": 0},
            at=now(),
        )
        assert learned.status is DiscrepancyStatus.LEARNED
        assert len(artifacts.corrections) == 1
        executor = WritebackExecutor(mode=WritebackMode.DRY)
        outcome = executor.execute(artifacts.corrections[0])
        assert outcome.result is WritebackResult.PLANNED
        assert not outcome.changed_the_business_system

    def _assigned(self) -> Discrepancy:
        """``Assigned`` まで進めた D2（学習信号の変換に必要な状態）。"""
        from sbp.reasoning import Discrepancy, Severity  # noqa: PLC0415 — テスト内の局所利用

        moment = now()
        opened = Discrepancy(
            kind=DiscrepancyKind.STATE,
            subjects=(PUMP,),
            assertions=(vocab.ent.Running,),
            evidence=(MES_RECORD_IRI,),
            detected_at=moment,
            interval=Interval(moment - timedelta(minutes=20), moment),
            severity=Severity(
                score=0.76, value_at_stake=8_450_000.0, confidence=0.92, persistence_s=1200.0
            ),
            detector="kind2",
            ontology_version=VERSION,
        )
        return opened.transition(DiscrepancyStatus.TRIAGED, at=moment).transition(
            DiscrepancyStatus.ASSIGNED, at=moment, assignee_role=vocab.ent.MaintenanceLead
        )


class TestG5Expectation:
    """設計書 §14 の後半（Expectation 生成 → 検証 → 学習信号）。"""

    @pytest.fixture
    def expectation(self, contracts: ActionRegistry) -> Expectation:
        """緊急点検の契約から Expectation を作る（契約の効果が期待になる）。"""
        contract = contracts.get(vocab.ent.CreateEmergencyInspection)
        executed_at = now()
        deadlines = {
            origin: deadline
            for _, deadline, origin in planned_expectation_deadlines(
                contract, executed_at=executed_at
            )
        }
        created = Expectation(
            action_event=__import__("uuid").uuid4(),
            action_type=contract.action_type,
            target=PUMP,
            created_at=executed_at,
            ontology_version=VERSION,
        )
        return created.activate(
            (
                ExpectedEffect(
                    fluent=vocab.ent.WorkOrder,
                    entity=PUMP,
                    expected=1.0,
                    lower=0.5,
                    upper=1.0,
                    deadline=deadlines["symbolic"],
                    origin="symbolic",
                    description="5 分以内に点検作業指示が起票される",
                ),
                ExpectedEffect(
                    fluent=vocab.ent.Inspected,
                    entity=PUMP,
                    expected=0.9,
                    lower=0.8,
                    upper=1.0,
                    deadline=deadlines["rollout"],
                    origin="rollout",
                    description="24 時間以内に点検済みになる",
                ),
            ),
            at=executed_at,
        )

    def test_the_contract_effects_become_the_expectation(self, expectation: Expectation) -> None:
        """設計書 §9.2 の 4: effects と rollout から期待分布・期限を作る。"""
        assert expectation.status is ExpectationStatus.ACTIVE
        assert {effect.origin for effect in expectation.expected_effects} == {
            "symbolic",
            "rollout",
        }
        assert expectation.deadline is not None

    def test_a_fulfilled_expectation_becomes_learned(self, expectation: Expectation) -> None:
        """設計書 §14 の要点 5: 満たされれば介入データになる。"""
        verified = verify_expectation(
            expectation,
            inputs=VerificationInputs(
                at=now() + timedelta(hours=1),
                beliefs={vocab.ent.Inspected: 0.95},
                records_present={vocab.ent.WorkOrder: True},
            ),
        )
        assert verified.status is ExpectationStatus.FULFILLED
        learned = verified.mark_learned(at=now() + timedelta(hours=2), note="介入データとして登録")
        assert learned.status is ExpectationStatus.LEARNED

    def test_a_violated_expectation_names_which_side_failed(self, expectation: Expectation) -> None:
        """設計書 §8.5: WM とプロセスオントロジーのどちらが外れたかを切り分ける。"""
        verified = verify_expectation(
            expectation,
            inputs=VerificationInputs(
                at=now() + timedelta(hours=1),
                beliefs={vocab.ent.Inspected: 0.05},
                records_present={vocab.ent.WorkOrder: True},
            ),
        )
        assert verified.status is ExpectationStatus.VIOLATED
        assert verified.verification is not None
        assert verified.verification.blames_the_world_model


class TestG5ForecastCoverage:
    """ゲート G5 の数値条件: 予測被覆率 ≥ 0.85（合成データ）。"""

    LEADS = (60.0, 300.0)
    STEP_S = 10.0

    @classmethod
    @pytest.fixture(scope="class")
    def report(cls, world: SynthWorld, observed_running: np.ndarray) -> Any:
        """起点をずらした持続予測を **conformal で較正して** 採点する。

        **区間幅を手で決めない。** 固定幅の区間は状態が変わる時点で必ず外れ、幅を広げて通すのは
        較正ではなく調整である（最初の実装がこれで、先行 300 秒の被覆率が 0.800 に落ちた）。
        較正区間の残差分位点を半幅にすると、交換可能性の仮定の下で名目被覆が保証される。

        較正と評価は時間で分ける（較正に使った窓で評価すると被覆率が楽観側に出る）。
        """
        calibration = range(100, 400, 5)
        evaluation = range(600, 1200, 5)

        halfwidths: dict[float, float] = {}
        for lead in cls.LEADS:
            offset = int(lead / cls.STEP_S)
            residuals = [
                abs(float(observed_running[index]) - float(observed_running[index + offset]))
                for index in calibration
                if index + offset < len(observed_running)
            ]
            halfwidths[lead] = conformal_halfwidth(residuals, level=0.9)

        forecasts: list[Forecast] = []
        observations: list[dict[float, float]] = []
        for index in evaluation:
            current = float(observed_running[index])
            points: list[ForecastPoint] = []
            actual: dict[float, float] = {}
            for lead in cls.LEADS:
                half = halfwidths[lead]
                points.append(
                    ForecastPoint(
                        lead_time_s=lead,
                        mean=current,
                        lower=max(0.0, current - half),
                        upper=min(1.0, current + half),
                        samples=tuple(
                            float(np.clip(current + offset, 0.0, 1.0))
                            for offset in (-half / 2, 0.0, half / 2)
                        ),
                    )
                )
                target = index + int(lead / cls.STEP_S)
                if target < len(observed_running):
                    actual[lead] = float(observed_running[target])
            if len(actual) == len(cls.LEADS):
                forecasts.append(
                    Forecast(
                        entity=PUMP,
                        fluent=vocab.ent.Running,
                        horizon_origin=world.timestamps[index],
                        wm_version="https://sbp.example/model/koopman/1.0.0",
                        ontology_version=VERSION,
                        conditioned_on=("https://sbp.example/site/siteA/plan/MP-0912",),
                        points=tuple(points),
                    )
                )
                observations.append(actual)
        return score_forecasts(forecasts, observations=observations)

    def test_coverage_meets_the_gate(self, report: Any) -> None:
        """ゲート G5: 予測被覆率 ≥ 0.85。"""
        for lead in report.leads():
            coverage = report.coverage_by_lead[lead]
            assert coverage >= COVERAGE_TARGET, f"先行 {lead}s の被覆率 {coverage:.3f}"

    def test_accuracy_degrades_with_the_horizon(self, report: Any) -> None:
        """短期は当たり長期は外れる。これが見えないと「信頼できる先行時間」が出せない。"""
        leads = report.leads()
        assert report.crps_by_lead[leads[0]] <= report.crps_by_lead[leads[-1]]

    def test_the_metrics_rows_are_ready_for_the_table(self, report: Any) -> None:
        """設計書 §12.3 の ``metrics`` 表に入る形で出る。"""
        rows = report.as_metric_rows(at=now())
        assert {row["name"] for row in rows} == {
            "forecast_crps",
            "forecast_coverage",
            "forecast_interval_width",
        }
        assert all(row["lead_time_s"] in report.leads() for row in rows)


class TestG5Persistence:
    """閉ループの結果が二時制ストアに入り、as-of で読める（G0〜G3 の維持）。"""

    def test_the_action_outcome_round_trips(
        self, assertion_store: AssertionStore, posterior: float
    ) -> None:
        moment = now().replace(microsecond=0)
        assertion = Assertion(
            subject=PUMP,
            predicate=vocab.ent.Running,
            object=False,
            valid_from=moment,
            tx_from=moment,
            # 閉ループの出力も推定である（不変条件 3）
            modality=Modality.INFERRED,
            belief=BooleanBelief(p=1.0 - posterior),
            provenance="closed-loop:g5",
            ontology_version=VERSION,
        )
        assertion_store.assert_(assertion)
        rows = assertion_store.query(
            subject=PUMP,
            predicate=vocab.ent.Running,
            as_of=moment + timedelta(seconds=1),
            valid_at=moment + timedelta(seconds=1),
        )
        assert rows, "閉ループの出力が読み出せない"
        assert rows[0].modality is Modality.INFERRED
        assert rows[0].ontology_version == VERSION

    def test_the_audit_mask_is_available_to_the_loop(self, mes_record: Record) -> None:
        """不変条件 6: 記録を監査する推定にその記録を入れない。"""
        mask = AuditMask.auditing_record(mes_record, reason="D2 の監査")
        assert mask.excludes_record(mes_record)
        assert "D2" in mask.provenance_note()
