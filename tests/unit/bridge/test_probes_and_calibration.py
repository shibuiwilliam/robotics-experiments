"""接地層の単体テスト（設計書 §5.2、§5.6、不変条件 7）。

重点は**昇格ゲートが不適格なプローブを拒否すること**。通ることだけを確かめると
「何も止めないゲート」を見逃す。CLAUDE.md の禁止事項「静的精度だけで active にする」が
実際に防がれているかを検査する。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from sbp.bridge.alignment import (
    alignment_residual,
    equipment_maintenance_transition,
    symbolize,
)
from sbp.bridge.calibration import (
    ConformalRegressor,
    TemperatureScaler,
    reliability_diagram,
)
from sbp.bridge.mil import TimeBag, fit_with_bags, mil_loss
from sbp.bridge.probes import (
    LinearProbe,
    ProbeKind,
    ProbeMetrics,
    ProbeSpec,
    ProbeStatus,
    auroc,
    expected_calibration_error,
    promote,
)
from sbp.core.config import BridgeSettings
from sbp.core.errors import BridgeError, CalibrationError, ProbePromotionError
from sbp.core.time import Interval
from sbp.testing import LATENT_DIM, Fluent, SynthConfig, SynthWorld

T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
VERSION = "0.2.0"


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    """4 サイクルの合成世界（分割しても両クラスが入る）。"""
    return SynthWorld(config=SynthConfig(start=T0, duration=timedelta(hours=4), cycles=4), seed=42)


def _spec(**overrides: object) -> ProbeSpec:
    defaults: dict[str, object] = {
        "probe_id": "https://sbp.example/site/siteA/probe/running-v1",
        "grounds": "https://sbp.example/ent#Running",
        "kind": ProbeKind.CLASSIFIER,
        "wm_version": "https://sbp.example/site/siteA/wm/synth-1.0",
        "ontology_version": VERSION,
    }
    return ProbeSpec(**(defaults | overrides))  # type: ignore[arg-type]


def _metrics(**overrides: object) -> ProbeMetrics:
    defaults: dict[str, object] = {
        "auroc": 0.95,
        "ece": 0.02,
        "alignment_residual": 0.03,
        "n_samples": 500,
    }
    return ProbeMetrics(**(defaults | overrides))  # type: ignore[arg-type]


class TestMetricsComputation:
    def test_auroc_of_a_perfect_ranker_is_one(self) -> None:
        labels = np.array([False, False, True, True])
        scores = np.array([0.1, 0.2, 0.8, 0.9])
        assert auroc(labels, scores) == pytest.approx(1.0)

    def test_auroc_of_a_random_ranker_is_half(self) -> None:
        labels = np.array([True, False, True, False])
        scores = np.array([0.5, 0.5, 0.5, 0.5])
        assert auroc(labels, scores) == pytest.approx(0.5)

    def test_auroc_of_an_inverted_ranker_is_zero(self) -> None:
        labels = np.array([True, True, False, False])
        scores = np.array([0.1, 0.2, 0.8, 0.9])
        assert auroc(labels, scores) == pytest.approx(0.0)

    def test_auroc_requires_both_classes(self) -> None:
        """片方のクラスしか無ければ AUROC は定義されない（黙って値を返さない）。"""
        with pytest.raises(BridgeError, match="undefined"):
            auroc(np.array([True, True]), np.array([0.1, 0.9]))

    def test_ece_of_a_perfectly_calibrated_predictor_is_near_zero(self) -> None:
        """確率 p と言ったものが実際に p の割合で成立すれば ECE はほぼ 0。"""
        rng = np.random.default_rng(0)
        probabilities = rng.uniform(0.0, 1.0, size=20_000)
        labels = rng.uniform(0.0, 1.0, size=20_000) < probabilities
        assert expected_calibration_error(labels, probabilities) < 0.02

    def test_ece_detects_overconfidence(self) -> None:
        """自信過剰（0.99 と言うのに半分しか当たらない）を検出する。"""
        probabilities = np.full(1000, 0.99)
        labels = np.array([i % 2 == 0 for i in range(1000)])
        assert expected_calibration_error(labels, probabilities) > 0.45


class TestLinearProbeOnSyntheticLatents:
    def test_probe_learns_the_concept(self, world: SynthWorld) -> None:
        """合成潜在は線形分離できる設計なので、線形プローブで学習できる。

        これは「潜在の品質検査」の基準系である（設計書 §5.2）。実 WM に差し替えて
        線形で足りなければ、潜在の質の問題として現れる。
        """
        latents = world.latents("mid")
        labels = world.oracle.labels_for(Fluent.RUNNING)
        cut = int(len(labels) * 0.6)

        probe = LinearProbe(dim=LATENT_DIM, seed=1)
        probe.fit(latents[:cut], labels[:cut], epochs=400)
        assert auroc(labels[cut:], probe.logits(latents[cut:])) > 0.9

    def test_untrained_probe_refuses_to_predict(self) -> None:
        probe = LinearProbe(dim=LATENT_DIM)
        with pytest.raises(BridgeError, match="not trained"):
            probe.predict(np.zeros((2, LATENT_DIM)))

    def test_uncalibrated_probe_refuses_to_predict(self, world: SynthWorld) -> None:
        """不変条件 7: 較正されていない値は belief になれない。"""
        latents = world.latents("mid")
        labels = world.oracle.labels_for(Fluent.RUNNING)
        probe = LinearProbe(dim=LATENT_DIM, seed=1).fit(latents, labels, epochs=50)
        with pytest.raises(BridgeError, match="no calibrator"):
            probe.predict(latents)

    def test_dimension_mismatch_is_caught(self) -> None:
        probe = LinearProbe(dim=LATENT_DIM, seed=1)
        with pytest.raises(BridgeError, match="dimension mismatch"):
            probe.logits(np.zeros((3, LATENT_DIM + 1)))

    def test_output_separates_epistemic_and_aleatoric_uncertainty(self, world: SynthWorld) -> None:
        """設計書 §5.6: 2 種の不確実性を分けて返す（スケジューラは認識的だけを狙う）。"""
        latents = world.latents("mid")
        labels = world.oracle.labels_for(Fluent.RUNNING)
        probe = LinearProbe(dim=LATENT_DIM, seed=1).fit(latents, labels, epochs=200)
        probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents), labels))

        without = probe.predict(latents, ensemble_noise=0.0)
        with_noise = probe.predict(latents, ensemble_noise=0.05)
        assert float(without.epistemic.sum()) == 0.0, "摂動なしなら認識的不確実性は 0"
        assert float(with_noise.epistemic.sum()) > 0.0, "摂動を与えれば認識的不確実性が出る"
        assert float(with_noise.aleatoric.max()) <= 0.25 + 1e-9, "ベルヌーイ分散の上限"

    def test_raw_scores_are_kept_separate_from_probabilities(self, world: SynthWorld) -> None:
        """不変条件 7: 生スコアは evidence 用に別に返す。"""
        latents = world.latents("mid")
        labels = world.oracle.labels_for(Fluent.RUNNING)
        probe = LinearProbe(dim=LATENT_DIM, seed=1).fit(latents, labels, epochs=200)
        probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents), labels))
        out = probe.predict(latents)
        assert not np.allclose(out.raw_scores, out.probabilities)
        assert (out.probabilities >= 0.0).all() and (out.probabilities <= 1.0).all()


class TestCalibration:
    def test_temperature_scaling_improves_ece(self, world: SynthWorld) -> None:
        """較正は ECE を改善する（順位は変えないので AUROC は保たれる）。"""
        latents = world.latents("mid")
        labels = world.oracle.labels_for(Fluent.RUNNING)
        cut = int(len(labels) * 0.6)
        probe = LinearProbe(dim=LATENT_DIM, seed=1).fit(latents[:cut], labels[:cut], epochs=400)

        raw = probe.logits(latents[cut:])
        uncalibrated = 1.0 / (1.0 + np.exp(-raw))
        before = expected_calibration_error(labels[cut:], uncalibrated)

        scaler = TemperatureScaler().fit(probe.logits(latents[:cut]), labels[:cut])
        probe.attach_calibrator(scaler)
        after = expected_calibration_error(labels[cut:], probe.predict(latents[cut:]).probabilities)

        assert after < before, f"較正が ECE を悪化させた: {before:.4f} -> {after:.4f}"
        # 順位は保たれる（AUROC が変わらない）
        assert auroc(labels[cut:], uncalibrated) == pytest.approx(
            auroc(labels[cut:], probe.predict(latents[cut:]).probabilities), abs=1e-9
        )

    def test_unfitted_scaler_refuses_to_apply(self) -> None:
        """不変条件 7: 未推定の較正器は使えない。"""
        with pytest.raises(CalibrationError, match="not fitted"):
            TemperatureScaler().apply(np.array([0.0, 1.0]))

    def test_single_class_holdout_is_rejected(self) -> None:
        """片方のクラスしか無い保留データでは温度が定まらない。"""
        with pytest.raises(CalibrationError, match="not identifiable"):
            TemperatureScaler().fit(np.array([1.0, 2.0]), np.array([True, True]))

    def test_empty_holdout_is_rejected(self) -> None:
        with pytest.raises(CalibrationError, match="empty hold-out"):
            TemperatureScaler().fit(np.array([]), np.array([], dtype=bool))

    def test_reliability_diagram_tracks_where_calibration_breaks(self) -> None:
        """信頼性ダイアグラムは「どこで」較正が崩れているかを示す。"""
        probabilities = np.concatenate([np.full(100, 0.1), np.full(100, 0.9)])
        labels = np.concatenate(
            [np.zeros(100, dtype=bool), np.ones(50, dtype=bool), np.zeros(50, dtype=bool)]
        )
        bins = reliability_diagram(labels, probabilities)
        low = next(b for b in bins if b[0] < 0.5)
        high = next(b for b in bins if b[0] > 0.5)
        low_gap = abs(low[0] - low[1])
        high_gap = abs(high[0] - high[1])
        # 0.1 と言って 0 回成立（乖離 0.1）より、0.9 と言って半分しか成立しない
        # （乖離 0.4）方がはるかに悪い。どこで崩れているかが分かるのが要点。
        assert high_gap > low_gap * 3, (
            f"崩れている側を特定できていない: low={low_gap} high={high_gap}"
        )
        assert high_gap > 0.3, "高確率側の崩れが検出できていない"
        assert low[2] == 100 and high[2] == 100, "各ビンの標本数も返る"


class TestConformalRegressor:
    def test_empirical_coverage_matches_target(self) -> None:
        """conformal は目標被覆率を経験的に満たす（設計書 §13.1 の被覆率指標）。"""
        rng = np.random.default_rng(0)
        calibration_pred = rng.normal(0.0, 1.0, size=2000)
        calibration_actual = calibration_pred + rng.normal(0.0, 0.5, size=2000)
        regressor = ConformalRegressor(coverage=0.9).fit(calibration_pred, calibration_actual)

        test_pred = rng.normal(0.0, 1.0, size=2000)
        test_actual = test_pred + rng.normal(0.0, 0.5, size=2000)
        coverage = regressor.empirical_coverage(test_pred, test_actual)
        assert 0.86 <= coverage <= 0.94, f"被覆率が目標から外れた: {coverage}"

    def test_interval_widens_with_higher_coverage(self) -> None:
        rng = np.random.default_rng(0)
        pred = rng.normal(0.0, 1.0, size=1000)
        actual = pred + rng.normal(0.0, 0.5, size=1000)
        narrow = ConformalRegressor(coverage=0.8).fit(pred, actual).interval(0.0)
        wide = ConformalRegressor(coverage=0.99).fit(pred, actual).interval(0.0)
        assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])

    def test_unfitted_regressor_refuses(self) -> None:
        with pytest.raises(CalibrationError, match="not fitted"):
            ConformalRegressor().interval(0.0)


class TestMultipleInstanceLearning:
    def test_weak_labels_train_the_probe(self, world: SynthWorld) -> None:
        """記録由来の時間バッグだけでプローブが学習できる（設計書 §5.2）。"""
        truth = world.oracle
        latents = world.latents("mid")
        bags = [
            TimeBag(concept="Running", entity="P-101", interval=iv, positive=True, weight=0.9)
            for iv in truth.intervals[Fluent.RUNNING]
        ]
        bags += [
            TimeBag(concept="Running", entity="P-101", interval=iv, positive=False, weight=0.9)
            for iv in truth.intervals[Fluent.FAULTED]
        ]
        probe = LinearProbe(dim=LATENT_DIM, seed=2)
        history = fit_with_bags(
            probe, latents, bags, truth.timestamps, epochs=300, learning_rate=0.3
        )

        assert history["final_loss"] < history["initial_loss"], "MIL 損失が下がっていない"
        labels = truth.labels_for(Fluent.RUNNING)
        assert auroc(labels, probe.logits(latents)) > 0.9

    def test_positive_bag_loss_rewards_any_high_probability(self) -> None:
        """正例バッグは「少なくとも 1 つ成立」で満たされる（全部高くする必要はない）。"""
        one_high = mil_loss(np.array([0.95, 0.05, 0.05]), positive=True)
        all_low = mil_loss(np.array([0.05, 0.05, 0.05]), positive=True)
        assert one_high < all_low

    def test_negative_bag_loss_requires_all_low(self) -> None:
        """負例バッグは全標本が低くなければならない。"""
        all_low = mil_loss(np.array([0.05, 0.05, 0.05]), positive=False)
        one_high = mil_loss(np.array([0.95, 0.05, 0.05]), positive=False)
        assert all_low < one_high

    def test_bag_weight_scales_the_loss(self) -> None:
        """信頼性の低い情報源のバッグは軽く効く（設計書 §5.2 のラベル重み）。"""
        heavy = mil_loss(np.array([0.1]), positive=True, weight=1.0)
        light = mil_loss(np.array([0.1]), positive=True, weight=0.2)
        assert light < heavy

    def test_empty_bag_is_rejected(self) -> None:
        with pytest.raises(BridgeError, match="empty bag"):
            mil_loss(np.array([]), positive=True)

    def test_invalid_bag_weight_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="weight must be in"):
            TimeBag(
                concept="Running",
                entity="P-101",
                interval=Interval(T0, T0 + timedelta(hours=1)),
                positive=True,
                weight=1.5,
            )

    def test_training_without_bags_is_rejected(self, world: SynthWorld) -> None:
        probe = LinearProbe(dim=LATENT_DIM)
        with pytest.raises(BridgeError, match="zero bags"):
            fit_with_bags(probe, world.latents("mid"), [], world.oracle.timestamps)

    def test_bags_outside_the_timeline_are_rejected(self, world: SynthWorld) -> None:
        """どのバッグにも標本が入らなければ学習できない（黙って通さない）。"""
        far_future = Interval(T0 + timedelta(days=365), T0 + timedelta(days=366))
        bags = [TimeBag(concept="Running", entity="P-101", interval=far_future, positive=True)]
        with pytest.raises(BridgeError, match="no bag contains any sample"):
            fit_with_bags(
                LinearProbe(dim=LATENT_DIM), world.latents("mid"), bags, world.oracle.timestamps
            )


class TestAlignmentResidual:
    def test_a_stable_series_is_aligned(self) -> None:
        """記号遷移が許す系列は残差が小さい。"""
        states = [{"Running": True, "UnderMaintenance": False}] * 10
        report = alignment_residual(states, equipment_maintenance_transition())
        assert report.residual == pytest.approx(0.0)
        assert report.is_aligned

    def test_flickering_series_has_a_large_residual(self) -> None:
        """ちらつく系列は静的精度が高くても残差が大きい。

        これが「AUROC だけで昇格させない」理由である（CLAUDE.md の禁止事項）。
        """
        states = [{"Running": i % 2 == 0, "UnderMaintenance": False} for i in range(20)]
        report = alignment_residual(states, equipment_maintenance_transition())
        assert report.flicker_rate > 0.4
        assert not report.is_aligned

    def test_exclusive_violation_is_counted(self) -> None:
        """排他フルーエントの同時成立を数える（SHACL の Warning 形状と同趣旨）。"""
        states = [
            {"Running": True, "UnderMaintenance": False},
            {"Running": True, "UnderMaintenance": True},
        ]
        report = alignment_residual(states, equipment_maintenance_transition())
        assert report.exclusive_violations == 1
        assert report.violations == 1

    def test_too_few_states_is_rejected(self) -> None:
        with pytest.raises(BridgeError, match="at least 2 states"):
            alignment_residual([{"Running": True}], equipment_maintenance_transition())

    def test_symbolize_turns_probabilities_into_symbols(self) -> None:
        symbols = symbolize({"Running": np.array([0.9, 0.2, 0.7])}, threshold=0.5)
        assert [s["Running"] for s in symbols] == [True, False, True]

    def test_symbolize_rejects_mismatched_lengths(self) -> None:
        with pytest.raises(BridgeError, match="mismatched lengths"):
            symbolize({"a": np.array([0.1, 0.2]), "b": np.array([0.3])})

    def test_real_probe_output_is_aligned(self, world: SynthWorld) -> None:
        """実際に学習したプローブの出力が記号的に整合している。"""
        latents = world.latents("mid")
        labels = world.oracle.labels_for(Fluent.RUNNING)
        probe = LinearProbe(dim=LATENT_DIM, seed=1).fit(latents, labels, epochs=400)
        probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents), labels))
        probabilities = probe.predict(latents).probabilities

        maintenance = np.zeros_like(probabilities)
        states = symbolize({"Running": probabilities, "UnderMaintenance": maintenance})
        report = alignment_residual(states, equipment_maintenance_transition())
        assert report.residual < 0.1, f"学習済みプローブの整合残差が大きい: {report}"


class TestPromotionGate:
    """昇格ゲート（設計書 §5.2）。3 条件の AND が実際に効くことを検査する。"""

    def test_a_good_probe_is_promoted(self) -> None:
        spec = _spec(metrics=_metrics())
        promoted = promote(spec, settings=BridgeSettings(), active_alignment_residuals=(0.05, 0.06))
        assert promoted.status is ProbeStatus.ACTIVE

    def test_low_auroc_blocks_promotion(self) -> None:
        with pytest.raises(ProbePromotionError, match="AUROC"):
            promote(
                _spec(metrics=_metrics(auroc=0.70)),
                settings=BridgeSettings(),
                active_alignment_residuals=(0.05,),
            )

    def test_high_ece_blocks_promotion_even_with_perfect_auroc(self) -> None:
        """**静的精度だけでは通らない**（CLAUDE.md の禁止事項そのもの）。"""
        with pytest.raises(ProbePromotionError, match="ECE"):
            promote(
                _spec(metrics=_metrics(auroc=1.0, ece=0.30)),
                settings=BridgeSettings(),
                active_alignment_residuals=(0.05,),
            )

    def test_high_alignment_residual_blocks_promotion_even_with_perfect_auroc_and_ece(
        self,
    ) -> None:
        """動的接地条件も AND の一部（設計原則 P2）。"""
        with pytest.raises(ProbePromotionError, match="alignment residual"):
            promote(
                _spec(metrics=_metrics(auroc=1.0, ece=0.001, alignment_residual=0.5)),
                settings=BridgeSettings(),
                active_alignment_residuals=(0.05, 0.06),
            )

    def test_all_failing_criteria_are_reported(self) -> None:
        """落ちた条件をすべて挙げる（1 つ直して再挑戦の無駄を避ける）。"""
        with pytest.raises(ProbePromotionError) as exc:
            promote(
                _spec(metrics=_metrics(auroc=0.5, ece=0.5, alignment_residual=0.9)),
                settings=BridgeSettings(),
                active_alignment_residuals=(0.05,),
            )
        message = str(exc.value)
        assert "AUROC" in message
        assert "ECE" in message
        assert "alignment residual" in message

    def test_probe_without_metrics_cannot_be_promoted(self) -> None:
        with pytest.raises(ProbePromotionError, match="no metrics"):
            promote(_spec(), settings=BridgeSettings(), active_alignment_residuals=())

    def test_first_probe_skips_the_median_comparison(self) -> None:
        """既存 active が無ければ中央値の条件は適用しない（比較対象が無い）。"""
        promoted = promote(
            _spec(metrics=_metrics(alignment_residual=0.4)),
            settings=BridgeSettings(),
            active_alignment_residuals=(),
        )
        assert promoted.status is ProbeStatus.ACTIVE

    def test_active_spec_requires_metrics(self) -> None:
        """検証されていないものを active と名乗れない。"""
        with pytest.raises(ValueError, match="must carry metrics"):
            _spec(status=ProbeStatus.ACTIVE)

    def test_thresholds_come_from_settings(self) -> None:
        """閾値は Settings から来る（コードに埋め込まない）。"""
        lenient = BridgeSettings(promote_min_auroc=0.5, promote_max_ece=0.5)
        promote(
            _spec(metrics=_metrics(auroc=0.6, ece=0.4)),
            settings=lenient,
            active_alignment_residuals=(),
        )

    def test_validity_domain_restricts_applicability(self) -> None:
        """有効ドメイン（拠点・運転モード）の外では使わない（設計書 §5.2）。"""
        spec = _spec(metrics=_metrics(), validity_sites=("siteA",), operating_modes=("normal",))
        assert spec.applies_to(site="siteA", operating_mode="normal")
        assert not spec.applies_to(site="siteB")
        assert not spec.applies_to(site="siteA", operating_mode="startup")

    def test_empty_validity_domain_means_everywhere(self) -> None:
        spec = _spec(metrics=_metrics())
        assert spec.applies_to(site="anything", operating_mode="anything")


class TestEndToEndGrounding:
    """潜在 → プローブ → 較正 → 評価 → 昇格 の一連が通る（G2 の中核）。"""

    def test_full_pipeline_promotes_a_probe(self, world: SynthWorld) -> None:
        truth = world.oracle
        latents = world.latents("mid")
        labels = truth.labels_for(Fluent.RUNNING)
        cut = int(len(labels) * 0.6)

        # 学習 → 較正 → 評価
        probe = LinearProbe(dim=LATENT_DIM, seed=1).fit(latents[:cut], labels[:cut], epochs=400)
        probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents[:cut]), labels[:cut]))

        probabilities = probe.predict(latents[cut:]).probabilities
        states = symbolize(
            {"Running": probabilities, "UnderMaintenance": np.zeros_like(probabilities)}
        )
        report = alignment_residual(states, equipment_maintenance_transition())
        metrics = probe.evaluate(latents[cut:], labels[cut:], alignment_residual=report.residual)

        # 設計書 §13.1 の Phase 1 目標を満たすか
        assert metrics.auroc >= 0.85, f"AUROC が目標未達: {metrics.auroc}"
        assert metrics.ece <= 0.05, f"ECE が目標未達: {metrics.ece}"

        spec = _spec(metrics=metrics)
        promoted = promote(spec, settings=BridgeSettings(), active_alignment_residuals=())
        assert promoted.status is ProbeStatus.ACTIVE
        assert promoted.metrics is not None
