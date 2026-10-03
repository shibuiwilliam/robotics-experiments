"""監査隔離と信念更新の単体テスト（不変条件 6、設計書 §8.1、§14）。

重点:

1. **監査隔離がマスクなしで迂回できない** — 記録尤度の計算は ``mask`` を必須引数に取る。
2. **設計書 §14 のトレースを再現する** — 計画と記録は稼働、観測は停止 → 事後 0.14 付近、
   観測が支配。
3. **三源寄与率が来歴に残る** — 説明サービス（M3-07）が使う。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sbp.core import vocab
from sbp.core.errors import AuditIsolationError, ReasoningError
from sbp.core.modality import Modality
from sbp.core.records import PlanItem, Record
from sbp.integration.reliability import ReliabilityRegistry
from sbp.reasoning.audit import AuditMask, no_audit
from sbp.reasoning.belief import (
    BeliefFilter,
    DwellTime,
    TransitionPrior,
    update_belief,
)

T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
STOP_AT = datetime(2026, 9, 12, 9, 41, 30, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]
PUMP = "https://sbp.example/site/siteA/asset/P-101"
VERSION = vocab.ONTOLOGY_VERSION


@pytest.fixture(scope="module")
def reliability() -> dict[tuple[str, str], object]:
    registry = ReliabilityRegistry.from_directory(REPO_ROOT / "reliability")
    keys = tuple(registry.keys())
    return {key: registry.get(*key) for key in keys}


@pytest.fixture
def plan() -> PlanItem:
    """設計書 §14: 09:00-17:00 稼働予定、遵守率 0.9。"""
    return PlanItem(
        plan_type=vocab.ent.MaintenancePlan,
        source=vocab.ent.CmmsSource,
        external_id="MP-0912",
        target=PUMP,
        planned_interval_start=T0,
        planned_interval_end=T0 + timedelta(hours=8),
        intended_fluent=vocab.ent.Running,
        adherence_prior=0.9,
        ontology_version=VERSION,
    )


@pytest.fixture
def mes_record() -> Record:
    """設計書 §14: MES は稼働中と記録している。"""
    return Record(
        record_type=vocab.ent.OperationLog,
        source=vocab.ent.MesSource,
        external_id="OL-0912-A",
        is_about=PUMP,
        recorded_at=T0 + timedelta(minutes=5),
        occurred_at=T0,
        payload={"running": 1},
        ontology_version=VERSION,
    )


@pytest.fixture
def transition() -> TransitionPrior:
    return TransitionPrior(
        dwell={"running": DwellTime(median_s=8 * 3600)}, step=timedelta(seconds=10)
    )


class TestAuditMask:
    """不変条件 6 の実装（設計書 §4.4）。"""

    def test_mask_must_state_what_it_audits(self, mes_record: Record) -> None:
        """理由の無いマスクは作れない（隔離の事実を来歴で検証できなくなる）。"""
        with pytest.raises(AuditIsolationError, match="must state what it is auditing"):
            AuditMask.auditing_record(mes_record, reason="")

    def test_auditing_a_record_excludes_it(self, mes_record: Record) -> None:
        mask = AuditMask.auditing_record(mes_record, reason="不一致 D2 の監査")
        assert mask.excludes_record(mes_record)
        assert not mask.is_empty

    def test_auditing_a_source_excludes_all_its_records(self, mes_record: Record) -> None:
        """情報源まるごとの監査（「MES の実績全体が疑わしい」状況）。"""
        mask = AuditMask.auditing_source(vocab.ent.MesSource, reason="MES 全体の信頼性調査")
        assert mask.excludes_record(mes_record)

    def test_no_audit_excludes_nothing(self, mes_record: Record) -> None:
        mask = no_audit()
        assert mask.is_empty
        assert not mask.excludes_record(mes_record)

    def test_filter_keeps_the_excluded_items(self, mes_record: Record) -> None:
        """除外したものも保持する（隔離が働いたことを後から検証できるように）。"""
        mask = AuditMask.auditing_record(mes_record, reason="監査")
        masked = mask.filter_records([mes_record])
        assert len(masked) == 0
        assert masked.exclusion_count == 1
        assert masked.is_isolated

    def test_provenance_note_records_the_isolation(self, mes_record: Record) -> None:
        """隔離の有無が来歴に残る。空のマスクでも「隔離なし」と明示する。"""
        audited = AuditMask.auditing_record(mes_record, reason="D2 の監査")
        assert "excluded" in audited.provenance_note()
        assert "D2 の監査" in audited.provenance_note()
        assert "none" in no_audit().provenance_note()

    def test_masks_merge(self, mes_record: Record) -> None:
        a = AuditMask.auditing_record(mes_record, reason="記録の監査")
        b = AuditMask.auditing_source(vocab.ent.ErpSource, reason="ERP の監査")
        merged = a.merge(b)
        assert merged.excludes_record(mes_record)
        assert vocab.ent.ErpSource in merged.audited_sources
        assert "記録の監査" in merged.reason and "ERP の監査" in merged.reason


class TestDwellTime:
    def test_survival_decreases_with_time(self) -> None:
        dwell = DwellTime(median_s=3600)
        assert dwell.survival(timedelta(0)) == pytest.approx(1.0)
        assert dwell.survival(timedelta(hours=1)) == pytest.approx(0.5, abs=0.01)
        assert dwell.survival(timedelta(hours=10)) < 0.01

    def test_shape_controls_memory(self) -> None:
        """shape > 1 は「続くほど終わりやすい」、< 1 は「続くほど続きやすい」。"""
        memoryless = DwellTime(median_s=3600, shape=1.0)
        aging = DwellTime(median_s=3600, shape=3.0)
        # 中位数の 2 倍の時点で、aging の方が生存確率が低い
        assert aging.survival(timedelta(hours=2)) < memoryless.survival(timedelta(hours=2))

    def test_invalid_parameters_are_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="median dwell time must be positive"):
            DwellTime(median_s=0.0)
        with pytest.raises(ReasoningError, match="shape must be positive"):
            DwellTime(median_s=100.0, shape=0.0)


class TestTransitionPrior:
    def test_markov_prior_tracks_the_previous_belief(self, transition: TransitionPrior) -> None:
        """長寿命の状態では事前が直前の事後をほぼ保つ（飽和しない）。

        初版の実装は継続確率を対数オッズに変換して足していたため、事前が 1 に飽和して
        観測が効かなくなっていた。継続確率は「崩れにくさ」であって「真である証拠」ではない。
        """
        log_odds, plan_part = transition.prior_log_odds(
            fluent="running", previous=0.95, elapsed=timedelta(minutes=41), plan=None, at=STOP_AT
        )
        prior = 1.0 / (1.0 + math.exp(-log_odds))
        assert prior == pytest.approx(0.95, abs=0.01), f"事前が飽和している: {prior}"
        assert plan_part == 0.0

    def test_plan_is_blended_not_added(self, transition: TransitionPrior, plan: PlanItem) -> None:
        """計画は混合として入る（ダイナミクスを上書きしない）。"""
        without, _ = transition.prior_log_odds(
            fluent="running", previous=0.2, elapsed=timedelta(minutes=5), plan=None, at=STOP_AT
        )
        with_plan, plan_part = transition.prior_log_odds(
            fluent="running", previous=0.2, elapsed=timedelta(minutes=5), plan=plan, at=STOP_AT
        )
        p_without = 1.0 / (1.0 + math.exp(-without))
        p_with = 1.0 / (1.0 + math.exp(-with_plan))
        assert p_without < p_with < plan.adherence_prior, (
            "計画が混合になっていない（上書きか無効化されている）"
        )
        assert plan_part > 0.0, "計画の寄与が記録されていない"

    def test_plan_outside_its_interval_has_no_effect(
        self, transition: TransitionPrior, plan: PlanItem
    ) -> None:
        after = T0 + timedelta(hours=10)
        _, plan_part = transition.prior_log_odds(
            fluent="running", previous=0.5, elapsed=timedelta(minutes=5), plan=plan, at=after
        )
        assert plan_part == 0.0

    def test_initial_state_uses_the_plan(self, transition: TransitionPrior, plan: PlanItem) -> None:
        """初期状態では計画が事前になる（他に情報が無い）。"""
        log_odds, _ = transition.prior_log_odds(
            fluent="running", previous=None, elapsed=timedelta(0), plan=plan, at=T0
        )
        assert 1.0 / (1.0 + math.exp(-log_odds)) == pytest.approx(0.9, abs=0.01)

    def test_invalid_weights_are_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="plan_weight must be in"):
            TransitionPrior(plan_weight=1.5)
        with pytest.raises(ReasoningError, match="start_rate must be in"):
            TransitionPrior(start_rate=1.0)


class TestThreeSourceFusion:
    """三源統合（設計書 §8.1）。"""

    def test_audit_mask_is_required(
        self,
        transition: TransitionPrior,
        mes_record: Record,
        reliability: dict[tuple[str, str], object],
    ) -> None:
        """不変条件 6: マスクを省略して呼べない（キーワード必須引数）。"""
        with pytest.raises(TypeError, match="mask"):
            update_belief(  # type: ignore[call-arg]
                fluent="running",
                entity=PUMP,
                at=STOP_AT,
                previous=0.95,
                elapsed=timedelta(minutes=41),
                transition=transition,
                records=[(mes_record, True)],
                reliability=reliability,  # type: ignore[arg-type]
            )

    def test_records_without_reliability_models_are_rejected(
        self, transition: TransitionPrior, mes_record: Record
    ) -> None:
        """未知の情報源を既定で信頼しない（設計書 §7.3）。"""
        with pytest.raises(ReasoningError, match="without reliability models"):
            update_belief(
                fluent="running",
                entity=PUMP,
                at=STOP_AT,
                previous=0.95,
                elapsed=timedelta(minutes=41),
                transition=transition,
                records=[(mes_record, True)],
                reliability=None,
                mask=no_audit(),
            )

    def test_observation_against_record_lowers_the_posterior(
        self,
        transition: TransitionPrior,
        mes_record: Record,
        plan: PlanItem,
        reliability: dict[tuple[str, str], object],
    ) -> None:
        """観測が記録と食い違うと事後が下がる（設計書 §14 の状況）。"""
        agreeing = update_belief(
            fluent="running",
            entity=PUMP,
            at=STOP_AT,
            previous=0.95,
            elapsed=timedelta(minutes=41),
            transition=transition,
            probe_probabilities=[0.95],
            records=[(mes_record, True)],
            reliability=reliability,  # type: ignore[arg-type]
            plan=plan,
            mask=no_audit(),
        )
        disagreeing = update_belief(
            fluent="running",
            entity=PUMP,
            at=STOP_AT,
            previous=0.95,
            elapsed=timedelta(minutes=41),
            transition=transition,
            probe_probabilities=[0.09],
            records=[(mes_record, True)],
            reliability=reliability,  # type: ignore[arg-type]
            plan=plan,
            mask=no_audit(),
        )
        assert disagreeing.posterior < agreeing.posterior

    def test_audit_isolation_removes_the_record_contribution(
        self,
        transition: TransitionPrior,
        mes_record: Record,
        plan: PlanItem,
        reliability: dict[tuple[str, str], object],
    ) -> None:
        """不変条件 6: 監査対象の記録は尤度に効かない。"""
        update = update_belief(
            fluent="running",
            entity=PUMP,
            at=STOP_AT,
            previous=0.95,
            elapsed=timedelta(minutes=41),
            transition=transition,
            probe_probabilities=[0.09],
            records=[(mes_record, True)],
            reliability=reliability,  # type: ignore[arg-type]
            plan=plan,
            mask=AuditMask.auditing_record(mes_record, reason="不一致 D2 の監査"),
        )
        assert update.contribution.record == 0.0, "監査対象の記録が尤度に入っている"
        assert update.excluded_records == 1
        assert "excluded" in update.audit_note

    def test_contribution_identifies_the_dominant_source(
        self,
        transition: TransitionPrior,
        mes_record: Record,
        plan: PlanItem,
        reliability: dict[tuple[str, str], object],
    ) -> None:
        """三源寄与率が来歴に残り、支配源が分かる（設計書 §8.1）。"""
        update = update_belief(
            fluent="running",
            entity=PUMP,
            at=STOP_AT,
            previous=0.5,
            elapsed=timedelta(minutes=41),
            transition=transition,
            probe_probabilities=[0.02],
            records=[(mes_record, True)],
            reliability=reliability,  # type: ignore[arg-type]
            plan=plan,
            mask=no_audit(),
        )
        assert update.contribution.dominant == "observed"
        contrib = update.contribution.as_dict()
        assert set(contrib) == {"observed", "record", "plan"}
        assert all(v >= 0.0 for v in contrib.values())

    def test_output_is_an_inferred_assertion(
        self, transition: TransitionPrior, reliability: dict[tuple[str, str], object]
    ) -> None:
        """不変条件 3: 信念更新の出力は必ず inferred。"""
        del reliability
        update = update_belief(
            fluent="running",
            entity=PUMP,
            at=STOP_AT,
            previous=0.5,
            elapsed=timedelta(minutes=1),
            transition=transition,
            probe_probabilities=[0.8],
            mask=no_audit(),
        )
        assertion = update.to_assertion(
            predicate=vocab.ent.Running,
            provenance="https://sbp.example/site/siteA/prov/belief-update",
            ontology_version=VERSION,
        )
        assert assertion.modality is Modality.INFERRED


class TestForwardFilterReproducesDesign14:
    """設計書 §14 のトレースを前向きフィルタで再現する。

    §14 の「事後 0.14」は 1 ステップで 0.95 から落ちた値ではなく、停止後に観測が続くことで
    収束する値である（設計書 §8.1 が毎刻み呼ばれる前向きフィルタとして書かれている）。
    """

    def _run(
        self,
        transition: TransitionPrior,
        mes_record: Record,
        plan: PlanItem,
        reliability: dict[tuple[str, str], object],
    ) -> list[object]:
        stamps = [T0 + timedelta(seconds=10 * i) for i in range(300)]
        stop_index = 249  # 09:41:30
        series = [[0.95] if i < stop_index else [0.09] for i in range(300)]
        filter_ = BeliefFilter(fluent="running", entity=PUMP, transition=transition)
        return filter_.run(  # type: ignore[return-value]
            stamps,
            probe_series=series,
            records=[(mes_record, True)],
            reliability=reliability,  # type: ignore[arg-type]
            plan=plan,
            mask=no_audit(),
        )

    def test_posterior_converges_near_the_design_value(
        self,
        transition: TransitionPrior,
        mes_record: Record,
        plan: PlanItem,
        reliability: dict[tuple[str, str], object],
    ) -> None:
        """**設計書 §14 の事後 0.14 を再現する。**"""
        updates = self._run(transition, mes_record, plan, reliability)
        final = updates[-1]
        assert 0.10 <= final.posterior <= 0.20, (  # type: ignore[attr-defined]
            f"設計書 §14 の事後 0.14 から外れた: {final.posterior:.4f}"  # type: ignore[attr-defined]
        )

    def test_belief_is_high_while_running(
        self,
        transition: TransitionPrior,
        mes_record: Record,
        plan: PlanItem,
        reliability: dict[tuple[str, str], object],
    ) -> None:
        """停止前は三源が一致しているので事後が高い。"""
        updates = self._run(transition, mes_record, plan, reliability)
        assert updates[200].posterior > 0.95  # type: ignore[attr-defined]

    def test_observation_dominates_after_the_stop(
        self,
        transition: TransitionPrior,
        mes_record: Record,
        plan: PlanItem,
        reliability: dict[tuple[str, str], object],
    ) -> None:
        """設計書 §14: 停止後は観測が事後を支配する。"""
        updates = self._run(transition, mes_record, plan, reliability)
        assert updates[-1].contribution.dominant == "observed"  # type: ignore[attr-defined]

    def test_the_drop_takes_several_steps(
        self,
        transition: TransitionPrior,
        mes_record: Record,
        plan: PlanItem,
        reliability: dict[tuple[str, str], object],
    ) -> None:
        """1 ステップでは落ちきらない（慣性が効いている）。

        半マルコフの滞在時間が「続いた状態は崩れにくい」という事前を与えるため、観測 1 回で
        信念が反転しない。これは「センサの瞬間値で信念を振り回さない」という設計の帰結である。
        """
        updates = self._run(transition, mes_record, plan, reliability)
        stop_index = 249
        assert updates[stop_index].posterior > 0.5, "停止直後の 1 ステップで反転している"  # type: ignore[attr-defined]
        assert updates[stop_index + 5].posterior < 0.5, "5 ステップ経っても落ちていない"  # type: ignore[attr-defined]

    def test_filter_tracks_dwell_time(self, transition: TransitionPrior) -> None:
        """状態が反転したら滞在時間がリセットされる（半マルコフの要点）。"""
        filter_ = BeliefFilter(fluent="running", entity=PUMP, transition=transition)
        for i in range(10):
            filter_.step(
                at=T0 + timedelta(seconds=10 * i), probe_probabilities=[0.95], mask=no_audit()
            )
        long_elapsed = filter_.elapsed
        assert long_elapsed > timedelta(seconds=50)

        # 状態を反転させる
        for i in range(10, 20):
            filter_.step(
                at=T0 + timedelta(seconds=10 * i), probe_probabilities=[0.01], mask=no_audit()
            )
        assert filter_.elapsed < long_elapsed, "反転後も滞在時間が積み上がっている"

    def test_mismatched_series_length_is_rejected(self, transition: TransitionPrior) -> None:
        filter_ = BeliefFilter(fluent="running", entity=PUMP, transition=transition)
        with pytest.raises(ReasoningError, match="entries but"):
            filter_.run([T0, T0 + timedelta(seconds=10)], probe_series=[[0.9]], mask=no_audit())
