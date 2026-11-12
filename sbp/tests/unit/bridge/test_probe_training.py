"""プローブの学習・評価・昇格（M8-23、ADR-0024）。

確かめるのは配線と不変条件: 弱ラベルの作り方、時間順の分割（漏れない）、データ不足と
ゲート不通過のとき**束を作らない**こと、真値（オラクル）を渡さずに学習が通ること。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from sbp.bridge.probes import LinearProbe
from sbp.bridge.training import (
    EntitySeries,
    _bags_of_slot,  # pyright: ignore[reportPrivateUsage]
    _core,  # pyright: ignore[reportPrivateUsage]
    _shift_intercept,  # pyright: ignore[reportPrivateUsage]
    _slices,  # pyright: ignore[reportPrivateUsage]
    bags_from_claims,
    train_probe,
)
from sbp.core import vocab
from sbp.core.belief import BooleanBelief
from sbp.core.config import BridgeSettings
from sbp.core.errors import BridgeError
from sbp.core.events import EventEnvelope, Producer, RecordEvidence, ValidTime
from sbp.core.ids import stable_event_id
from sbp.core.modality import Modality
from sbp.testing import SynthConfig, SynthWorld
from sbp.testing.synth import Fluent
from sbp.worldmodel.families.koopman import KoopmanConfig, KoopmanModel

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
FLUENT = "https://sbp.example/ent#Running"
PROBE = "https://sbp.example/probe/Running/1.0.0"
WM = "https://sbp.example/wm/koopman/0.1.0"


def _claim(
    at: datetime, p: float, *, uncertainty: float = 0.0, fluent: str = FLUENT
) -> EventEnvelope:
    return EventEnvelope(
        event_id=stable_event_id(at, f"{fluent}|{at.isoformat()}|{p}"),
        type=f"{vocab.core.BusinessEvent}/{fluent}",
        subject=PUMP,
        valid_time=ValidTime(start=at, uncertainty_s=uncertainty),
        tx_time=at,
        modality=Modality.OBSERVED,
        belief=BooleanBelief(p=p),
        evidence=(
            RecordEvidence(record="https://sbp.example/rec/1", source="https://sbp.example/src"),
        ),
        producer=Producer(agent="test", version="0"),
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


class TestBagsFromClaims:
    def test_probability_decides_polarity_and_weight(self) -> None:
        bags, unused = bags_from_claims(
            [_claim(T0, 0.9), _claim(T0, 0.2), _claim(T0, 0.5)],
            fluent=FLUENT,
            settings=BridgeSettings(),
        )
        assert [b.positive for b in bags] == [True, False]
        assert bags[0].weight == pytest.approx(0.8)
        assert bags[1].weight == pytest.approx(0.6)
        assert unused == 1, "p = 0.5 は情報が無いので使わない"

    def test_other_fluents_are_ignored(self) -> None:
        bags, _ = bags_from_claims(
            [_claim(T0, 0.9, fluent="https://sbp.example/ent#Faulted")],
            fluent=FLUENT,
            settings=BridgeSettings(),
        )
        assert bags == []

    def test_a_point_time_claim_still_gets_a_window(self) -> None:
        (bag,), _ = bags_from_claims([_claim(T0, 0.9)], fluent=FLUENT, settings=BridgeSettings())
        assert bag.interval.end is not None
        assert bag.interval.end - bag.interval.start == timedelta(seconds=600)

    def test_the_window_widens_with_the_source_clock_uncertainty(self) -> None:
        (bag,), _ = bags_from_claims(
            [_claim(T0, 0.9, uncertainty=900.0)], fluent=FLUENT, settings=BridgeSettings()
        )
        assert bag.interval.end is not None
        assert bag.interval.end - bag.interval.start == timedelta(seconds=1800)


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    return SynthWorld(config=SynthConfig(start=T0, duration=timedelta(hours=8), cycles=8), seed=31)


@pytest.fixture(scope="module")
def series(world: SynthWorld) -> EntitySeries:
    model = KoopmanModel.untrained(
        model_id="https://sbp.example/wm/koopman",
        version="0.1.0",
        domain="d",
        channels=world.channels,
        config=KoopmanConfig(delays=8),
    ).fit(world.observations, seed=31)
    latents = model.embed_series(world.observations)
    return EntitySeries(PUMP, world.timestamps[7:], latents)


def _claims_from(world: SynthWorld, every: int = 20) -> list[EventEnvelope]:
    """業務記録の台本。真値から「記録が言うこと」を作る（外界の模擬であり、学習には記録だけが渡る）。"""
    labels = world.oracle.labels_for(Fluent.RUNNING)
    return [
        _claim(world.timestamps[i], 0.9 if labels[i] else 0.1)
        for i in range(8, len(world.timestamps), every)
    ]


class TestTrainProbe:
    def _train(self, series: EntitySeries, claims: list[EventEnvelope], **kw: object):  # type: ignore[no-untyped-def]
        settings = BridgeSettings(weak_label_min_half_width_s=60.0, **kw)  # type: ignore[arg-type]
        bags, _ = bags_from_claims(claims, fluent=FLUENT, settings=settings)
        return train_probe(
            [series],
            bags,
            probe_id=PROBE,
            grounds=FLUENT,
            wm_version=WM,
            level="mid",
            settings=settings,
            seed=7,
        )

    def test_a_promoted_probe_carries_a_calibrated_bundle(
        self, world: SynthWorld, series: EntitySeries
    ) -> None:
        outcome = self._train(
            series, _claims_from(world), promote_min_auroc=0.7, promote_max_ece=0.2
        )
        assert outcome.metrics is not None
        assert outcome.bundle is not None, outcome.reasons
        assert outcome.bundle.calibration is not None, "不変条件 7: 較正済みだけ"
        assert outcome.spec.status.value == "active"
        assert outcome.bundle.spec.wm_version == WM

    def test_failing_the_gate_yields_no_bundle_and_says_why(
        self, world: SynthWorld, series: EntitySeries
    ) -> None:
        outcome = self._train(series, _claims_from(world), promote_max_ece=0.0)
        assert outcome.bundle is None
        assert outcome.spec.status.value == "candidate"
        assert outcome.metrics is not None
        assert any("ECE" in r for r in outcome.reasons)

    def test_no_labels_means_no_bundle(self, series: EntitySeries) -> None:
        outcome = self._train(series, [])
        assert outcome.bundle is None
        assert outcome.reasons

    def test_too_few_labels_in_the_held_out_windows_means_no_bundle(
        self, world: SynthWorld, series: EntitySeries
    ) -> None:
        outcome = self._train(series, _claims_from(world, every=400), probe_min_labelled=50)
        assert outcome.bundle is None
        assert outcome.reasons

    def test_the_split_is_chronological(self) -> None:
        train, calib, held = _slices(100, BridgeSettings())
        assert train.stop == calib.start and calib.stop == held.start
        assert (len(train), len(calib), len(held)) == (60, 20, 20)

    def test_a_split_that_leaves_no_evaluation_window_is_refused(self) -> None:
        with pytest.raises(ValueError, match="below 1"):
            BridgeSettings(probe_train_fraction=0.7, probe_calib_fraction=0.3)

    def test_mismatched_dimensions_are_refused(self, series: EntitySeries) -> None:
        other = EntitySeries("e2", series.timestamps, series.latents[:, :5])
        with pytest.raises(BridgeError, match="mismatched"):
            train_probe(
                [series, other],
                [],
                probe_id=PROBE,
                grounds=FLUENT,
                wm_version=WM,
                level="mid",
                settings=BridgeSettings(),
                seed=1,
            )

    def test_numpy_seed_makes_training_reproducible(
        self, world: SynthWorld, series: EntitySeries
    ) -> None:
        a = self._train(series, _claims_from(world), promote_min_auroc=0.0, promote_max_ece=1.0)
        b = self._train(series, _claims_from(world), promote_min_auroc=0.0, promote_max_ece=1.0)
        assert a.bundle is not None and b.bundle is not None
        assert np.allclose(a.bundle.weights, b.bundle.weights)


class TestReviewFixes:
    def test_claims_outside_the_latent_history_decline_instead_of_crashing(
        self, series: EntitySeries
    ) -> None:
        far = T0 - timedelta(days=30)
        bags, _ = bags_from_claims(
            [_claim(far, 0.9), _claim(far + timedelta(hours=1), 0.1)],
            fluent=FLUENT,
            settings=BridgeSettings(),
        )
        outcome = train_probe(
            [series],
            bags,
            probe_id=PROBE,
            grounds=FLUENT,
            wm_version=WM,
            level="mid",
            settings=BridgeSettings(),
            seed=1,
        )
        assert outcome.bundle is None
        assert outcome.reasons

    def test_one_polarity_only_declines(self, world: SynthWorld, series: EntitySeries) -> None:
        positives = [c for c in _claims_from(world) if c.belief.p > 0.5]  # type: ignore[union-attr]
        bags, _ = bags_from_claims(positives, fluent=FLUENT, settings=BridgeSettings())
        outcome = train_probe(
            [series],
            bags,
            probe_id=PROBE,
            grounds=FLUENT,
            wm_version=WM,
            level="mid",
            settings=BridgeSettings(),
            seed=1,
        )
        assert outcome.bundle is None
        assert any("both polarities" in r for r in outcome.reasons)

    def test_calibration_and_evaluation_never_share_a_claim(
        self, world: SynthWorld, series: EntitySeries
    ) -> None:
        bags, _ = bags_from_claims(
            _claims_from(world),
            fluent=FLUENT,
            settings=BridgeSettings(weak_label_min_half_width_s=60.0),
        )
        calib = _bags_of_slot(series, bags, BridgeSettings(), 1)
        held = _bags_of_slot(series, bags, BridgeSettings(), 2)
        assert calib and held
        assert not {id(b) for b in calib} & {id(b) for b in held}

    def test_two_entities_are_sliced_and_matched_separately(
        self, world: SynthWorld, series: EntitySeries
    ) -> None:
        other = EntitySeries(PUMP + "-2", series.timestamps, series.latents)
        claims = _claims_from(world)
        settings = BridgeSettings(
            weak_label_min_half_width_s=60.0, promote_min_auroc=0.7, promote_max_ece=0.2
        )
        bags, _ = bags_from_claims(claims, fluent=FLUENT, settings=settings)
        # 2 つ目の実体には主張が無い: その実体の標本は学習・評価に入らない
        outcome = train_probe(
            [series, other],
            bags,
            probe_id=PROBE,
            grounds=FLUENT,
            wm_version=WM,
            level="mid",
            settings=settings,
            seed=7,
        )
        assert outcome.bundle is not None, outcome.reasons
        assert outcome.metrics is not None
        assert outcome.metrics.n_samples == self._single(series, bags, settings)

    @staticmethod
    def _single(series: EntitySeries, bags: list, settings: BridgeSettings) -> int:  # type: ignore[type-arg]
        outcome = train_probe(
            [series],
            bags,
            probe_id=PROBE,
            grounds=FLUENT,
            wm_version=WM,
            level="mid",
            settings=settings,
            seed=7,
        )
        assert outcome.metrics is not None
        return outcome.metrics.n_samples


class TestInterceptAndCore:
    @pytest.mark.parametrize("offset", [0.0, 2.0, 10.0, 25.0, -25.0])
    def test_the_intercept_shift_converges_even_when_scores_are_far_off(
        self, offset: float
    ) -> None:
        """飽和したスコアでも発散せず、較正枠の陽性率に合う（ニュートン法は 10 以上で発散した）。"""
        rng = np.random.default_rng(0)
        latents = rng.normal(size=(400, 3))
        labels = latents[:, 0] > 0
        probe = LinearProbe(dim=3, seed=0)
        probe.load_parameters(weights=np.array([3.0, 0.0, 0.0]), bias=offset)
        _shift_intercept(probe, latents, labels)
        predicted = float(np.mean(1.0 / (1.0 + np.exp(-probe.logits(latents)))))
        assert predicted == pytest.approx(float(np.mean(labels)), abs=1e-3)
        assert abs(probe.bias) < 100.0

    def test_the_label_core_is_the_claim_instant_not_the_widened_window(self) -> None:
        bags, _ = bags_from_claims(
            [_claim(T0, 0.9, uncertainty=900.0)], fluent=FLUENT, settings=BridgeSettings()
        )
        core = _core(bags[0], timedelta(seconds=60))
        assert core.interval.end is not None
        assert core.interval.end - core.interval.start == timedelta(seconds=120)
        assert core.interval.start + timedelta(seconds=60) == T0
