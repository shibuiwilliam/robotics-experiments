"""記号条件付けとモデルレジストリの単体テスト（設計書 §4.4、§4.8）。

重点:

1. **マスクを省略して条件付けを作れない** — 不変条件 6 を型で強制する。
2. **特徴を黙って切り詰めない** — 次元が足りなければ構築時に落ちる。
3. **`latest` でロードできない** — 過去の信念の説明が静かに変わる。
4. **未評価のプローブで昇格できない** — 次元が同じでも潜在の意味は同じではない。
5. **1 回の閾値超過で再学習を起動しない** — 計測ノイズで学習ジョブが暴走する。
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from sbp.core import vocab
from sbp.core.audit import AuditMask, no_audit
from sbp.core.errors import ModelVersionError, WorldModelError
from sbp.core.records import Norm, NormSeverity, PlanItem, Record
from sbp.worldmodel import (
    CompatibilityEntry,
    ConditioningSpec,
    LatentLevel,
    LatentSpec,
    ModelHealth,
    ModelRegistry,
    ModelStatus,
    WorldModelMeta,
    build_context,
    evaluate_drift,
)

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
MODEL = "https://sbp.example/model/koopman"
VERSION = vocab.ONTOLOGY_VERSION
MIN_AUROC = 0.85
MAX_ECE = 0.05
MAX_RESIDUAL = 0.2


def _spec(**overrides: object) -> ConditioningSpec:
    values: dict[str, object] = {
        "fluents": (vocab.ent.Running, vocab.ent.Faulted),
        "latent_dim": 24,
    }
    values.update(overrides)
    return ConditioningSpec(**values)  # type: ignore[arg-type]


def _meta(
    version: str = "1.0.0", *, domain: str = "https://sbp.example/ent#Pump"
) -> WorldModelMeta:
    return WorldModelMeta(
        model_id=MODEL,
        version=version,
        domain=domain,
        modalities=("vibration_rms",),
        latent_spec=LatentSpec(dims={LatentLevel.MID: 24}),
    )


def _record(external_id: str = "OL-1") -> Record:
    return Record(
        record_type=vocab.ent.OperationLog,
        source=vocab.ent.MesSource,
        external_id=external_id,
        is_about=PUMP,
        recorded_at=T0,
        occurred_at=T0,
        payload={"running": 1},
        ontology_version=VERSION,
    )


class TestMaskIsMandatory:
    def test_build_requires_a_mask_keyword(self) -> None:
        """後から差し込む設計にすると、マスクを忘れた呼び出しが必ず残る。"""
        signature = inspect.signature(build_context)
        mask = signature.parameters["mask"]
        assert mask.kind is inspect.Parameter.KEYWORD_ONLY
        assert mask.default is inspect.Parameter.empty

    def test_an_audited_record_is_excluded_and_noted(self) -> None:
        record = _record()
        mask = AuditMask.auditing_record(record, reason="不一致 D2 の監査")
        context = build_context(
            PUMP, spec=_spec(), mask=mask, records=[record], beliefs={vocab.ent.Running: 0.9}
        )
        assert context.excluded == 1
        assert context.is_isolated
        assert "excluded" in context.audit_note

    def test_no_audit_states_that_nothing_was_isolated(self) -> None:
        context = build_context(PUMP, spec=_spec(), mask=no_audit(), records=[_record()])
        assert context.excluded == 0
        assert "none" in context.audit_note


class TestConditioningSpec:
    def test_it_refuses_to_truncate_features(self) -> None:
        """黙って落とすと、モデルが何に条件付けられているかが変わる。"""
        with pytest.raises(WorldModelError, match="is smaller than the"):
            _spec(latent_dim=4)

    def test_it_needs_at_least_one_fluent(self) -> None:
        with pytest.raises(WorldModelError, match="at least one fluent"):
            _spec(fluents=())

    def test_every_dimension_is_named(self) -> None:
        """無名の特徴ベクトルは、版が食い違ったとき黙って別の意味の数値が入る。"""
        spec = _spec()
        assert len(spec.describe()) == spec.latent_dim
        assert spec.describe()[0].startswith("belief:")


class TestContextContent:
    def test_an_unknown_belief_defaults_to_uninformative(self) -> None:
        """信念が無いことを「偽」にしない（0.5 = 分からない）。"""
        context = build_context(PUMP, spec=_spec(), mask=no_audit())
        assert context.features[f"belief:{vocab.ent.Running}"] == pytest.approx(0.5)

    def test_beliefs_land_in_declared_order(self) -> None:
        context = build_context(
            PUMP,
            spec=_spec(),
            mask=no_audit(),
            beliefs={vocab.ent.Running: 0.13, vocab.ent.Faulted: 0.80},
        )
        assert context.vector[0] == pytest.approx(0.13)
        assert context.vector[1] == pytest.approx(0.80)

    def test_an_out_of_range_belief_is_rejected(self) -> None:
        with pytest.raises(WorldModelError, match="must be a probability"):
            build_context(PUMP, spec=_spec(), mask=no_audit(), beliefs={vocab.ent.Running: 1.4})

    def test_the_type_hash_keeps_the_dimension_stable(self) -> None:
        """型が増えるたびに次元が変わると、学習済みモデルが使えなくなる。"""
        first = build_context(PUMP, spec=_spec(), mask=no_audit(), entity_type=vocab.ent.Pump)
        second = build_context(PUMP, spec=_spec(), mask=no_audit(), entity_type=vocab.ent.SparePart)
        assert first.vector.shape == second.vector.shape
        assert not (first.vector == second.vector).all()

    def test_the_plan_contributes_adherence_and_remaining_time(self) -> None:
        plan = PlanItem(
            plan_type=vocab.ent.MaintenancePlan,
            source=vocab.ent.CmmsSource,
            external_id="MP-1",
            target=PUMP,
            planned_interval_start=T0,
            planned_interval_end=T0 + timedelta(hours=8),
            intended_fluent=vocab.ent.Running,
            adherence_prior=0.9,
            ontology_version=VERSION,
        )
        context = build_context(
            PUMP, spec=_spec(), mask=no_audit(), plan=plan, at=T0 + timedelta(hours=2)
        )
        assert context.features["plan:adherence"] == pytest.approx(0.9)
        # 8 時間の計画の 2 時間後なので残りは 0.75
        assert context.features["plan:remaining_fraction"] == pytest.approx(0.75)

    def test_without_a_plan_the_plan_features_are_zero(self) -> None:
        context = build_context(PUMP, spec=_spec(), mask=no_audit())
        assert context.features["plan:adherence"] == 0.0
        assert context.features["plan:remaining_fraction"] == 0.0

    def test_the_strongest_norm_is_carried(self) -> None:
        norms = [
            Norm(
                norm_type=vocab.ent.MaintenancePlan,
                rule_ref=reference,
                source_document="https://sbp.example/doc/x",
                source_clause="1",
                severity=severity,
                applies_to_type=vocab.ent.Pump,
                effective_from=T0 - timedelta(days=1),
                ontology_version=VERSION,
            )
            for reference, severity in (
                ("advisory_rule", NormSeverity.ADVISORY),
                ("critical_rule", NormSeverity.CRITICAL),
            )
        ]
        context = build_context(PUMP, spec=_spec(), mask=no_audit(), norms=norms)
        assert context.features["norm:max_weight"] == pytest.approx(NormSeverity.CRITICAL.weight)

    def test_the_same_inputs_give_the_same_vector(self) -> None:
        """as-of 再構成: 当時の信念から当時の条件付けが再現できること。"""
        first = build_context(PUMP, spec=_spec(), mask=no_audit(), beliefs={vocab.ent.Running: 0.3})
        second = build_context(
            PUMP, spec=_spec(), mask=no_audit(), beliefs={vocab.ent.Running: 0.3}
        )
        assert (first.vector == second.vector).all()


class TestRegistryVersioning:
    def test_weights_live_under_the_version(self) -> None:
        registry = ModelRegistry()
        entry = registry.register(_meta())
        assert entry.weights_uri == "s3://models/koopman/1.0.0/"
        assert entry.status is ModelStatus.CANDIDATE

    def test_latest_cannot_be_loaded(self) -> None:
        """同じ版を名乗る別の重みが入り込むと、過去の信念の説明が静かに変わる。"""
        registry = ModelRegistry()
        registry.register(_meta())
        with pytest.raises(ModelVersionError, match="'latest' is forbidden"):
            registry.get(MODEL, "latest")

    def test_registering_the_same_version_twice_is_refused(self) -> None:
        registry = ModelRegistry()
        registry.register(_meta())
        with pytest.raises(ModelVersionError, match="already registered"):
            registry.register(_meta())

    def test_an_unknown_version_fails_loudly(self) -> None:
        registry = ModelRegistry()
        with pytest.raises(ModelVersionError, match="is not registered"):
            registry.get(MODEL, "9.9.9")

    def test_versions_are_listed_in_order(self) -> None:
        registry = ModelRegistry()
        for version in ("1.0.0", "1.2.0", "1.1.0"):
            registry.register(_meta(version))
        assert registry.versions_of(MODEL) == ("1.0.0", "1.1.0", "1.2.0")


class TestPromotion:
    def _ready(self) -> tuple[ModelRegistry, str]:
        registry = ModelRegistry()
        entry = registry.register(_meta())
        entry.record_compatibility(
            CompatibilityEntry(
                probe_id="probe:running",
                auroc=0.99,
                ece=0.01,
                align_residual=0.0,
                evaluated_at="2026-10-03T00:00:00Z",
            )
        )
        return registry, "probe:running"

    def test_canary_comes_before_active(self) -> None:
        """カナリア無しの全体昇格は設計書 §4.8 に反する。"""
        registry, probe = self._ready()
        with pytest.raises(ModelVersionError, match="cannot move"):
            registry.promote(
                MODEL,
                "1.0.0",
                ModelStatus.ACTIVE,
                required_probes=[probe],
                min_auroc=MIN_AUROC,
                max_ece=MAX_ECE,
                max_residual=MAX_RESIDUAL,
            )
        registry.promote(
            MODEL,
            "1.0.0",
            ModelStatus.CANARY,
            required_probes=[probe],
            min_auroc=MIN_AUROC,
            max_ece=MAX_ECE,
            max_residual=MAX_RESIDUAL,
        )
        entry = registry.promote(
            MODEL,
            "1.0.0",
            ModelStatus.ACTIVE,
            required_probes=[probe],
            min_auroc=MIN_AUROC,
            max_ece=MAX_ECE,
            max_residual=MAX_RESIDUAL,
        )
        assert entry.status is ModelStatus.ACTIVE

    def test_an_unevaluated_probe_blocks_promotion(self) -> None:
        """次元が同じだから動く、は最も危険な思い込み（動くが無意味になる）。"""
        registry, _ = self._ready()
        with pytest.raises(ModelVersionError, match="has not been evaluated"):
            registry.promote(
                MODEL,
                "1.0.0",
                ModelStatus.CANARY,
                required_probes=["probe:faulted"],
                min_auroc=MIN_AUROC,
                max_ece=MAX_ECE,
                max_residual=MAX_RESIDUAL,
            )

    def test_a_failing_probe_blocks_promotion(self) -> None:
        registry = ModelRegistry()
        entry = registry.register(_meta())
        entry.record_compatibility(
            CompatibilityEntry(
                probe_id="probe:running",
                auroc=0.99,
                ece=0.30,  # ECE が閾値超過
                align_residual=0.0,
                evaluated_at="2026-10-03T00:00:00Z",
            )
        )
        with pytest.raises(ModelVersionError, match="fails the gate"):
            registry.promote(
                MODEL,
                "1.0.0",
                ModelStatus.CANARY,
                required_probes=["probe:running"],
                min_auroc=MIN_AUROC,
                max_ece=MAX_ECE,
                max_residual=MAX_RESIDUAL,
            )

    def test_deprecated_is_terminal_but_loadable(self) -> None:
        """旧版の推定は tx 時間で残るため、説明のためにロードできる状態を保つ。"""
        registry, probe = self._ready()
        registry.promote(
            MODEL,
            "1.0.0",
            ModelStatus.DEPRECATED,
            min_auroc=MIN_AUROC,
            max_ece=MAX_ECE,
            max_residual=MAX_RESIDUAL,
        )
        with pytest.raises(ModelVersionError, match="cannot move"):
            registry.promote(
                MODEL,
                "1.0.0",
                ModelStatus.CANARY,
                required_probes=[probe],
                min_auroc=MIN_AUROC,
                max_ece=MAX_ECE,
                max_residual=MAX_RESIDUAL,
            )
        assert registry.get(MODEL, "1.0.0").status is ModelStatus.DEPRECATED

    def test_only_serving_models_are_listed(self) -> None:
        registry, probe = self._ready()
        assert registry.serving("https://sbp.example/ent#Pump") == ()
        registry.promote(
            MODEL,
            "1.0.0",
            ModelStatus.CANARY,
            required_probes=[probe],
            min_auroc=MIN_AUROC,
            max_ece=MAX_ECE,
            max_residual=MAX_RESIDUAL,
        )
        assert len(registry.serving("https://sbp.example/ent#Pump")) == 1


class TestDrift:
    def _health(self, **overrides: float) -> ModelHealth:
        values = {"latent_shift": 0.01, "pred_error": 0.05, "align_residual": 0.01}
        values.update(overrides)
        return ModelHealth(samples=100, wm_version=f"{MODEL}/1.0.0", **values)  # type: ignore[arg-type]

    def _registry(self) -> ModelRegistry:
        registry = ModelRegistry()
        registry.register(_meta())
        return registry

    def test_no_history_is_an_error(self) -> None:
        registry = self._registry()
        with pytest.raises(ModelVersionError, match="no health history"):
            evaluate_drift(
                registry.get(MODEL, "1.0.0"),
                shift_warn=0.1,
                error_warn=0.3,
                residual_warn=0.2,
            )

    def test_a_healthy_history_needs_no_action(self) -> None:
        registry = self._registry()
        for _ in range(3):
            registry.record_health(MODEL, "1.0.0", self._health())
        alert = evaluate_drift(
            registry.get(MODEL, "1.0.0"), shift_warn=0.1, error_warn=0.3, residual_warn=0.2
        )
        assert alert.action == "none"
        assert not alert.needs_attention

    def test_a_single_breach_only_triggers_shadow_evaluation(self) -> None:
        """単発の超過で再学習を回すと、計測ノイズで学習ジョブが暴走する。"""
        registry = self._registry()
        registry.record_health(MODEL, "1.0.0", self._health())
        registry.record_health(MODEL, "1.0.0", self._health())
        registry.record_health(MODEL, "1.0.0", self._health(latent_shift=0.9))
        alert = evaluate_drift(
            registry.get(MODEL, "1.0.0"), shift_warn=0.1, error_warn=0.3, residual_warn=0.2
        )
        assert alert.action == "shadow_evaluate"
        assert alert.breached == ("latent_shift",)

    def test_a_sustained_breach_triggers_retraining(self) -> None:
        registry = self._registry()
        for _ in range(3):
            registry.record_health(MODEL, "1.0.0", self._health(latent_shift=0.9))
        alert = evaluate_drift(
            registry.get(MODEL, "1.0.0"), shift_warn=0.1, error_warn=0.3, residual_warn=0.2
        )
        assert alert.action == "retrain"

    def test_the_trend_is_measured_from_the_first_report(self) -> None:
        registry = self._registry()
        registry.record_health(MODEL, "1.0.0", self._health(pred_error=0.05))
        registry.record_health(MODEL, "1.0.0", self._health(pred_error=0.40))
        alert = evaluate_drift(
            registry.get(MODEL, "1.0.0"), shift_warn=0.1, error_warn=0.3, residual_warn=0.2
        )
        assert alert.trend["pred_error"] == pytest.approx(0.35)

    def test_a_health_report_for_another_version_is_refused(self) -> None:
        registry = self._registry()
        other = ModelHealth(
            latent_shift=0.0,
            pred_error=0.0,
            align_residual=0.0,
            samples=1,
            wm_version=f"{MODEL}/2.0.0",
        )
        with pytest.raises(ModelVersionError, match="health report names"):
            registry.record_health(MODEL, "1.0.0", other)
