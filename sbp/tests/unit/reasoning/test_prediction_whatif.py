"""予測サービスと What-if の単体テスト（設計書 §8.3、§8.4、M5-05・M5-06）。

重点:

1. **CRPS は広い分布を罰する** — 「区間を広げて当てる」ができない。
2. **精度は先行時間ごとに分ける** — 全体平均は短期の当たりで長期の外れを隠す。
3. **hypothetical は隔離される** — シナリオ IRI 必須、昇格は promote_scenario のみ。
4. **反実仮想は昇格できない** — 起きなかった世界を信念にしない。
5. **妥当性スコアが外挿を可視化する** — 介入データ 0 件の反実仮想はスコア 0。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from sbp.core import vocab
from sbp.core.errors import ReasoningError
from sbp.core.modality import Modality
from sbp.reasoning import (
    Forecast,
    ForecastPoint,
    ScenarioKind,
    reliable_lead_time,
    score_forecasts,
)
from sbp.reasoning.prediction import (
    continuous_ranked_probability_score,
    interval_coverage,
)
from sbp.reasoning.whatif import validity_from_support

T0 = datetime(2026, 9, 12, 9, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
WM = "https://sbp.example/model/koopman/1.0.0"
VERSION = vocab.ONTOLOGY_VERSION


def _forecast(*, origin: datetime = T0, wm_version: str = WM) -> Forecast:
    return Forecast(
        entity=PUMP,
        fluent=vocab.ent.Running,
        horizon_origin=origin,
        wm_version=wm_version,
        ontology_version=VERSION,
        conditioned_on=("https://sbp.example/site/siteA/plan/MP-1",),
        points=(
            ForecastPoint(
                lead_time_s=60.0, mean=0.9, lower=0.85, upper=0.95, samples=(0.88, 0.9, 0.92)
            ),
            ForecastPoint(
                lead_time_s=600.0, mean=0.6, lower=0.2, upper=0.95, samples=(0.4, 0.6, 0.8)
            ),
        ),
    )


class TestScoringPrimitives:
    def test_crps_punishes_a_wide_distribution(self) -> None:
        """被覆率と違い、CRPS は「区間を広げて当てる」ことを許さない。"""
        sharp = continuous_ranked_probability_score(np.array([0.89, 0.90, 0.91]), 0.9)
        wide = continuous_ranked_probability_score(np.array([0.2, 0.9, 1.0]), 0.9)
        assert sharp < wide

    def test_crps_is_zero_for_a_perfect_point_forecast(self) -> None:
        assert continuous_ranked_probability_score(np.array([0.9]), 0.9) == pytest.approx(0.0)

    def test_crps_needs_samples(self) -> None:
        with pytest.raises(ReasoningError, match="at least one sample"):
            continuous_ranked_probability_score(np.array([]), 0.5)

    def test_coverage_counts_inclusion(self) -> None:
        assert interval_coverage([0.5, 0.9], [0.4, 0.95], [0.6, 1.0]) == pytest.approx(0.5)

    def test_coverage_needs_matching_lengths(self) -> None:
        with pytest.raises(ReasoningError, match="matching lengths"):
            interval_coverage([0.5], [0.1, 0.2], [0.9])


class TestForecast:
    def test_a_forecast_must_name_its_origin_and_model(self) -> None:
        """起点が無いと「何分先まで当たるか」を測れない。"""
        forecast = _forecast()
        assert forecast.horizon_origin == T0
        assert forecast.wm_version == WM
        assert forecast.horizon_s == 600.0

    def test_duplicated_lead_times_are_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="non-increasing or duplicated"):
            Forecast(
                entity=PUMP,
                fluent=vocab.ent.Running,
                horizon_origin=T0,
                wm_version=WM,
                ontology_version=VERSION,
                points=(
                    ForecastPoint(lead_time_s=60.0, mean=0.9, lower=0.8, upper=1.0),
                    ForecastPoint(lead_time_s=60.0, mean=0.5, lower=0.4, upper=0.6),
                ),
            )

    def test_a_plan_change_triggers_a_refresh(self) -> None:
        """設計書 §8.3: 計画変更時は再予測をトリガ。"""
        forecast = _forecast()
        assert forecast.needs_refresh(plan_changed=["https://sbp.example/site/siteA/plan/MP-1"])
        assert not forecast.needs_refresh(plan_changed=["https://sbp.example/site/siteA/plan/MP-2"])

    def test_a_point_outside_its_interval_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="outside its own interval"):
            ForecastPoint(lead_time_s=60.0, mean=0.9, lower=0.1, upper=0.5)


class TestScoring:
    def test_accuracy_is_tracked_per_lead_time(self) -> None:
        """全体平均は短期の当たりで長期の外れを隠す。"""
        report = score_forecasts(
            [_forecast(), _forecast(origin=T0 + timedelta(minutes=10))],
            observations=[{60.0: 0.91, 600.0: 0.55}, {60.0: 0.89, 600.0: 0.2}],
        )
        assert report.leads() == (60.0, 600.0)
        assert report.crps_by_lead[60.0] < report.crps_by_lead[600.0]
        assert report.samples_by_lead[60.0] == 2

    def test_mixing_wm_versions_is_refused(self) -> None:
        """精度は 1 つのモデルに帰属できなければ意味がない（設計書 §4.8）。"""
        with pytest.raises(ReasoningError, match="mixes WM versions"):
            score_forecasts(
                [_forecast(), _forecast(wm_version="other/2.0.0")],
                observations=[{60.0: 0.9}, {60.0: 0.9}],
            )

    def test_mixing_entities_is_refused(self) -> None:
        other = _forecast().model_copy(update={"entity": "https://sbp.example/x"})
        with pytest.raises(ReasoningError, match="mixes entities or fluents"):
            score_forecasts([_forecast(), other], observations=[{60.0: 0.9}, {60.0: 0.9}])

    def test_observations_must_match_some_lead_time(self) -> None:
        with pytest.raises(ReasoningError, match="no forecast point had a matching observation"):
            score_forecasts([_forecast()], observations=[{999.0: 0.5}])

    def test_the_reliable_lead_time_stops_at_the_first_failure(self) -> None:
        """一度崩れた予測が、さらに先で偶然当たっても「そこまで信頼できる」とは言えない。"""
        report = score_forecasts(
            [_forecast(), _forecast(origin=T0 + timedelta(minutes=10))],
            observations=[{60.0: 0.91, 600.0: 0.55}, {60.0: 0.89, 600.0: 0.2}],
        )
        assert reliable_lead_time(report, max_crps=0.05) == 60.0
        assert reliable_lead_time(report, max_crps=0.0001) is None

    def test_metric_rows_carry_the_lead_time(self) -> None:
        report = score_forecasts([_forecast()], observations=[{60.0: 0.9, 600.0: 0.6}])
        rows = report.as_metric_rows(at=T0)
        names = {row["name"] for row in rows}
        assert names == {"forecast_crps", "forecast_coverage", "forecast_interval_width"}
        assert all(row["lead_time_s"] in (60.0, 600.0) for row in rows)
        assert all(row["wm_version"] == WM for row in rows)


class TestValidityScore:
    def test_no_observed_intervention_scores_zero(self) -> None:
        """介入を一度も観測していない反実仮想は形だけ同じ外挿である。"""
        score = validity_from_support(0)
        assert score.score == 0.0
        assert score.is_extrapolation
        assert "一度も観測されていない" in score.note

    def test_enough_support_scores_one(self) -> None:
        score = validity_from_support(10)
        assert score.score == 1.0
        assert not score.is_extrapolation

    def test_negative_support_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="must be non-negative"):
            validity_from_support(-1)


class TestScenarioGuards:
    def test_the_counterfactual_kind_is_causal(self) -> None:
        assert ScenarioKind.COUNTERFACTUAL.is_causal
        assert not ScenarioKind.INTERVENTION.is_causal
        assert not ScenarioKind.PLAN_DIFF.is_causal

    def test_hypothetical_is_the_only_modality_scenarios_produce(self) -> None:
        """不変条件 3: 検討したという事実によって信念になる経路を作らない。"""
        assert Modality.HYPOTHETICAL.requires_scenario
        assert not Modality.PLANNED.requires_scenario
