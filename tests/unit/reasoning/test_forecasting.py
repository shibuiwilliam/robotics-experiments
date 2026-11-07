"""予測の生成・書き出し・採点（M9-02、ADR-0032）。

重点:

1. **先行時間は刻み幅から決まり、地平の末端を必ず含む。**
2. **区間は平均を含む。標本は分布の根拠として残る。**
3. **予測は ``predicted``**（主信念に入らない）で、起点と先行時間を持つ。
4. **採点は信念が揃った点だけ** — 信念がまだ無い点は数えず、1 点も揃わなければ採点しない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import numpy as np
import pytest

from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.errors import ReasoningError
from sbp.core.modality import Modality
from sbp.knowledge import AssertionStore, EmbeddedGraphStore
from sbp.knowledge.forecasts import ForecastRecord
from sbp.reasoning.forecasting import (
    forecast_assertions,
    make_forecast,
    missing_assertions,
    prediction_set,
    realised_outcome,
    score_due,
)
from sbp.worldmodel.base import LatentLevel, LatentState, Trajectory

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from numpy.typing import NDArray

    from sbp.reasoning.prediction import Forecast

ENTITY = "https://sbp.example/site/siteA/asset/P-101"
FLUENT = str(vocab.ent.Running)
WM = "https://sbp.example/wm/koopman/1.0.0"
T0 = datetime(2026, 11, 8, 9, tzinfo=UTC)
STEP = timedelta(seconds=10)


class _Model:
    """z[0] が刻みごとに +1 ずつ増える決定的なモデル。標本ごとに +0.1 ずつずれる。"""

    def rollout(
        self, state: LatentState, *, horizon: int, samples: int, seed: int
    ) -> tuple[Trajectory, ...]:
        del seed
        out: list[Trajectory] = []
        for sample in range(samples):
            states = tuple(
                LatentState(
                    z=np.array([step + 1 + 0.1 * sample]),
                    level=state.level,
                    at=state.at + STEP * (step + 1),
                    wm_version=state.wm_version,
                    covariance=np.eye(1),
                )
                for step in range(horizon)
            )
            out.append(Trajectory(states=states, horizon_origin=state.at))
        return tuple(out)


def _probe(latents: NDArray[np.float64]) -> dict[str, NDArray[np.float64]]:
    return {"Running": np.clip(1.0 - latents[:, 0] / 20.0, 0.0, 1.0)}


def _state() -> LatentState:
    return LatentState(
        z=np.zeros(1), level=LatentLevel.MID, at=T0, wm_version=WM, covariance=np.eye(1)
    )


def _constant(value: float):  # type: ignore[no-untyped-def]
    def probe(latents: NDArray[np.float64]) -> dict[str, NDArray[np.float64]]:
        return {"Running": np.full(latents.shape[0], value)}

    return probe


def _other(latents: NDArray[np.float64]) -> dict[str, NDArray[np.float64]]:
    return {"Other": latents[:, 0]}


def _forecast(
    *,
    probe: Callable[[NDArray[np.float64]], Mapping[str, NDArray[np.float64]]] = _probe,
    horizon_steps: int = 12,
    stride: int = 5,
    samples: int = 10,
) -> Forecast:
    return make_forecast(
        model=_Model(),
        probe=probe,
        entity=ENTITY,
        fluent=FLUENT,
        state=_state(),
        horizon_steps=horizon_steps,
        stride=stride,
        samples=samples,
        interval_level=0.9,
        seed=1,
        ontology_version=vocab.ONTOLOGY_VERSION,
    )


class TestMakeForecast:
    def test_leads_follow_the_stride_and_include_the_horizon_end(self) -> None:
        forecast = _forecast()
        assert [p.lead_time_s for p in forecast.points] == [50.0, 100.0, 120.0]

    def test_the_interval_contains_the_mean_and_samples_are_kept(self) -> None:
        forecast = _forecast()
        for point in forecast.points:
            assert point.lower <= point.mean <= point.upper
            assert len(point.samples) == 10
            assert point.mean == pytest.approx(float(np.mean(point.samples)))

    def test_farther_leads_are_less_certain_in_this_model(self) -> None:
        forecast = _forecast()
        assert forecast.points[0].mean > forecast.points[-1].mean

    def test_a_fluent_without_a_probe_is_refused(self) -> None:
        with pytest.raises(ReasoningError, match="no probe grounds"):
            _forecast(probe=_other)

    def test_the_forecast_names_its_model_version(self) -> None:
        assert _forecast().wm_version == WM


class TestAssertions:
    def test_a_forecast_becomes_predicted_assertions_with_origin_and_lead(self) -> None:
        forecast = _forecast()
        written = forecast_assertions(forecast, tx_from=T0)
        assert len(written) == len(forecast.points)
        for assertion, point in zip(written, forecast.points, strict=True):
            assert assertion.modality is Modality.PREDICTED
            assert assertion.horizon_origin == T0
            assert assertion.lead_time == timedelta(seconds=point.lead_time_s)
            assert assertion.valid_from == T0 + timedelta(seconds=point.lead_time_s)
            assert isinstance(assertion.belief, BooleanBelief)
            assert assertion.belief.p == pytest.approx(point.mean)

    def test_predicted_assertions_never_reach_the_main_belief_query(self) -> None:
        store = AssertionStore(EmbeddedGraphStore(), site_id="siteA")
        for assertion in forecast_assertions(_forecast(), tx_from=T0):
            store.assert_(assertion)
        inferred = store.query(subject=ENTITY, predicate=FLUENT, modality=Modality.INFERRED)
        assert inferred == []


def _belief(
    store: AssertionStore, at: datetime, p: float, provenance: str = "urn:sbp:prov:belief-worker/x"
) -> None:
    store.assert_(
        Assertion(
            subject=ENTITY,
            predicate=FLUENT,
            object=True,
            valid_from=at,
            tx_from=at,
            modality=Modality.INFERRED,
            belief=BooleanBelief(p=p),
            provenance=provenance,
            ontology_version=vocab.ONTOLOGY_VERSION,
        )
    )


def _record(forecast: Forecast) -> ForecastRecord:
    return ForecastRecord(
        forecast_id=uuid4(),
        entity=forecast.entity,
        fluent=forecast.fluent,
        horizon_origin=forecast.horizon_origin,
        horizon_end=forecast.points[-1].at(forecast.horizon_origin),
        wm_version=forecast.wm_version,
        interval_level=forecast.interval_level,
        payload=forecast.model_dump(mode="json"),
        scored_at=None,
    )


class TestScoring:
    def test_the_realised_outcome_is_the_latest_belief_at_that_time(self) -> None:
        store = AssertionStore(EmbeddedGraphStore(), site_id="siteA")
        _belief(store, T0, 0.9)
        _belief(store, T0 + timedelta(seconds=60), 0.1)
        at = T0 + timedelta(seconds=90)
        assert realised_outcome(store, entity=ENTITY, fluent=FLUENT, at=at) == 0.0
        early = T0 + timedelta(seconds=30)
        assert realised_outcome(store, entity=ENTITY, fluent=FLUENT, at=early) == 1.0
        assert realised_outcome(store, entity=ENTITY, fluent=FLUENT, at=T0 - STEP) is None

    def test_scoring_emits_crps_and_coverage_per_lead(self) -> None:
        store = AssertionStore(EmbeddedGraphStore(), site_id="siteA")
        _belief(store, T0 - STEP, 0.9)
        forecast = _forecast()
        samples, scored = score_due([_record(forecast)], store=store, at=T0 + timedelta(hours=1))
        assert len(scored) == 1
        names = {m.name.key for m in samples}
        assert names == {"forecast_crps", "forecast_coverage"}
        assert len(samples) == 2 * len(forecast.points)
        for sample in samples:
            assert 0.0 <= sample.value <= 1.0
            assert sample.labels["fluent"] == "Running"
            assert sample.labels["wm_version"] == WM

    def test_a_forecast_with_no_resolved_point_is_not_scored(self) -> None:
        store = AssertionStore(EmbeddedGraphStore(), site_id="siteA")  # 信念がまだ無い
        samples, scored = score_due([_record(_forecast())], store=store, at=T0 + timedelta(hours=1))
        assert samples == []
        assert scored == []

    def test_a_correct_forecast_scores_better_than_a_wrong_one(self) -> None:
        store = AssertionStore(EmbeddedGraphStore(), site_id="siteA")
        _belief(store, T0 - STEP, 0.99)  # 実測は「成立」
        good = _forecast(probe=_constant(0.95))
        bad = _forecast(probe=_constant(0.05))

        def mean_crps(forecast: Forecast) -> float:
            samples, _ = score_due([_record(forecast)], store=store, at=T0 + timedelta(hours=1))
            return float(np.mean([m.value for m in samples if m.name.key == "forecast_crps"]))

        assert mean_crps(good) < mean_crps(bad)

    def test_crps_is_the_brier_score_of_the_mean_not_of_the_spread(self) -> None:
        """確率を 0 / 1 に割った予測（平均は同じ）が、認識的な広がりで有利にならない。"""
        store = AssertionStore(EmbeddedGraphStore(), site_id="siteA")
        _belief(store, T0 - STEP, 0.99)  # 実測は「成立」

        def spread(latents: NDArray[np.float64]) -> dict[str, NDArray[np.float64]]:
            return {"Running": np.where(np.arange(latents.shape[0]) % 2 == 0, 0.0, 1.0)}

        def at_half(latents: NDArray[np.float64]) -> dict[str, NDArray[np.float64]]:
            return {"Running": np.full(latents.shape[0], 0.5)}

        def crps(forecast: Forecast) -> float:
            samples, _ = score_due([_record(forecast)], store=store, at=T0 + timedelta(hours=1))
            return float(np.mean([m.value for m in samples if m.name.key == "forecast_crps"]))

        assert crps(_forecast(probe=spread)) == pytest.approx(crps(_forecast(probe=at_half)))
        assert crps(_forecast(probe=at_half)) == pytest.approx(0.25)

    @pytest.mark.parametrize(
        ("p", "expected"),
        [(0.95, {1}), (0.05, {0}), (0.5, {0, 1}), (0.9, {1}), (0.1, {0})],
    )
    def test_the_prediction_set_at_nominal_ninety_percent(
        self, p: float, expected: set[int]
    ) -> None:
        assert prediction_set(p, 0.9) == frozenset(expected)

    def test_coverage_is_one_when_the_confident_side_is_right_and_zero_when_wrong(self) -> None:
        store = AssertionStore(EmbeddedGraphStore(), site_id="siteA")
        _belief(store, T0 - STEP, 0.99)  # 実測は「成立」

        def coverage(
            probe: Callable[[NDArray[np.float64]], Mapping[str, NDArray[np.float64]]],
        ) -> float:
            samples, _ = score_due(
                [_record(_forecast(probe=probe))], store=store, at=T0 + timedelta(hours=1)
            )
            return float(np.mean([m.value for m in samples if m.name.key == "forecast_coverage"]))

        assert coverage(_constant(0.95)) == 1.0
        assert coverage(_constant(0.05)) == 0.0
        assert coverage(_constant(0.5)) == 1.0

    def test_beliefs_from_other_provenance_are_not_the_realised_outcome(self) -> None:
        store = AssertionStore(EmbeddedGraphStore(), site_id="siteA")
        _belief(store, T0, 0.9)
        _belief(store, T0 + timedelta(seconds=30), 0.1, provenance="urn:sbp:prov:probe/raw")
        _belief(
            store,
            T0 + timedelta(seconds=40),
            0.1,
            provenance="urn:sbp:prov:belief-worker/coupling/2026-11-08T09:00:00+00:00",
        )
        at = T0 + timedelta(seconds=60)
        assert realised_outcome(store, entity=ENTITY, fluent=FLUENT, at=at) == 1.0

    def test_a_partial_forecast_waits_for_late_beliefs_then_scores_what_exists(self) -> None:
        store = AssertionStore(EmbeddedGraphStore(), site_id="siteA")
        forecast = _forecast()
        # 最初の点の時刻より後に信念が 1 件だけ（それ以前の信念は無い）→ どの点も揃わない
        _belief(store, forecast.points[-1].at(T0), 0.9)
        late = T0 + timedelta(hours=1)
        _, scored = score_due(
            [_record(forecast)], store=store, at=late, complete_wait=timedelta(days=1)
        )
        assert scored == [], "全点が揃うまで待つ"
        # 待つ上限を過ぎたら揃った点だけで採点する
        samples, scored = score_due(
            [_record(forecast)], store=store, at=late, complete_wait=timedelta(0)
        )
        assert len(scored) == 1
        assert {m.labels["lead_time_s"] for m in samples} == {repr(forecast.points[-1].lead_time_s)}


class TestIdempotentAssertions:
    def test_missing_points_are_filled_after_an_interruption(self) -> None:
        store = AssertionStore(EmbeddedGraphStore(), site_id="siteA")
        forecast = _forecast()
        written = missing_assertions(forecast, store=store, tx_from=T0)
        assert len(written) == len(forecast.points)
        store.assert_(written[0])  # 1 件目だけ書いて落ちた
        rest = missing_assertions(forecast, store=store, tx_from=T0)
        assert [a.valid_from for a in rest] == [a.valid_from for a in written[1:]]
        for assertion in rest:
            store.assert_(assertion)
        assert missing_assertions(forecast, store=store, tx_from=T0) == []
