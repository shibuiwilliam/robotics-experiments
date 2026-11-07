"""予測の生成・書き出し・採点（M9-02、ADR-0032）。

``sbp.reasoning.prediction`` は型と採点の関数を持つが、予測を**作る**者がいなかった。ここが
それである: WM のロールアウト（潜在の標本軌道）にプローブを通し、フルーエントの成立確率の
分布を先行時間ごとに作る。

- **確率はプローブの較正済み出力**（不変条件 7）。軌道の標本ごとに確率を出し、その標本が
  分布になる。区間は標本の分位点、平均は標本平均。
- **予測は ``predicted``**（不変条件 3）。主信念には入らない。起点（``horizon_origin``）違いで
  共存する。
- **採点の「実測」は信念の事後**（belief-worker の最新の推定が 0.5 以上なら 1）。真値ではない
  ——運用には真値が無い。信念が誤っていれば採点も誤る。これは予測の「物理に対する精度」
  ではなく「基盤の信念との整合」である（ADR-0032 の限界。真値は ``sbp.testing`` 以外から
  読めない: 不変条件 10）。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

import numpy as np

from sbp.bridge.runner import fluent_name
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger
from sbp.core.metrics import MetricSample, metric
from sbp.core.modality import Modality
from sbp.reasoning.prediction import Forecast, ForecastPoint, score_forecasts

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

    from numpy.typing import NDArray

    from sbp.knowledge.assertions import AssertionStore
    from sbp.knowledge.forecasts import ForecastRecord
    from sbp.worldmodel.base import LatentState, Trajectory

__all__ = [
    "Rollouter",
    "forecast_assertions",
    "make_forecast",
    "realised_outcome",
    "score_due",
]

log = get_logger(__name__)


class Rollouter(Protocol):
    """ロールアウトできるモデル（``WorldModel`` の一部）。"""

    def rollout(
        self, state: LatentState, *, horizon: int, samples: int, seed: int
    ) -> tuple[Trajectory, ...]:
        """地平 ``horizon`` の軌道を ``samples`` 本返す。"""
        ...


def make_forecast(
    *,
    model: Rollouter,
    probe: Callable[[NDArray[np.float64]], Mapping[str, NDArray[np.float64]]],
    entity: str,
    fluent: str,
    state: LatentState,
    horizon_steps: int,
    stride: int,
    samples: int,
    interval_level: float,
    seed: int,
    ontology_version: str,
) -> Forecast:
    """起点の潜在から、フルーエントの成立確率の予測を作る。

    Args:
        model: ロールアウトするモデル。
        probe: 潜在の行列 → フルーエント別の較正済み確率（``ProbeSet``）。
        entity: 実体 IRI。
        fluent: フルーエント型 IRI。
        state: 起点の潜在（不確実性を持つこと）。
        horizon_steps: 地平（刻み数）。
        stride: 先行時間の間隔（刻み数）。地平の末端は必ず含める。
        samples: 軌道の本数。
        interval_level: 予測区間の名目被覆。
        seed: 乱数シード。
        ontology_version: オントロジー版。

    Returns:
        予測。

    Raises:
        ReasoningError: プローブがこのフルーエントを出さない、または軌道が空の場合。
    """
    name = fluent_name(fluent)
    trajectories = model.rollout(state, horizon=horizon_steps, samples=samples, seed=seed)
    if not trajectories:
        raise ReasoningError("the model returned no trajectories")
    indices = sorted({*range(stride - 1, horizon_steps, stride), horizon_steps - 1})
    low_q, high_q = (1.0 - interval_level) / 2.0, 1.0 - (1.0 - interval_level) / 2.0
    points: list[ForecastPoint] = []
    for index in indices:
        latents = np.vstack([t.states[index].z for t in trajectories])
        outputs = probe(latents)
        if name not in outputs:
            raise ReasoningError(
                f"no probe grounds {name!r}; a forecast of it would be made of nothing"
            )
        probabilities = np.clip(np.asarray(outputs[name], dtype=np.float64), 0.0, 1.0)
        mean = float(probabilities.mean())
        lead = (trajectories[0].states[index].at - state.at).total_seconds()
        points.append(
            ForecastPoint(
                lead_time_s=lead,
                mean=mean,
                lower=min(float(np.quantile(probabilities, low_q)), mean),
                upper=max(float(np.quantile(probabilities, high_q)), mean),
                samples=tuple(float(p) for p in probabilities),
            )
        )
    return Forecast(
        entity=entity,
        fluent=fluent,
        horizon_origin=state.at,
        points=tuple(points),
        wm_version=state.wm_version,
        interval_level=interval_level,
        ontology_version=ontology_version,
    )


def forecast_assertions(forecast: Forecast, *, tx_from: datetime) -> list[Assertion]:
    """予測を ``predicted`` のアサーションにする（先行時間ごとに 1 件、平均）。

    Args:
        forecast: 予測。
        tx_from: 記録時刻。
    """
    return [
        Assertion(
            subject=forecast.entity,
            predicate=forecast.fluent,
            object=True,
            valid_from=point.at(forecast.horizon_origin),
            tx_from=tx_from,
            modality=Modality.PREDICTED,
            belief=BooleanBelief(p=point.mean),
            horizon_origin=forecast.horizon_origin,
            lead_time=timedelta(seconds=point.lead_time_s),
            provenance=f"urn:sbp:prov:forecast/{forecast.forecast_id}",
            ontology_version=forecast.ontology_version,
        )
        for point in forecast.points
    ]


def realised_outcome(
    store: AssertionStore, *, entity: str, fluent: str, at: datetime
) -> float | None:
    """その時刻の信念（最新の推定）から 0 / 1 の実測を作る。信念がまだ無ければ ``None``。

    Args:
        store: アサーションストア。
        entity: 実体 IRI。
        fluent: フルーエント型 IRI。
        at: 対象の時刻。
    """
    rows = store.query(subject=entity, predicate=fluent, valid_at=at, modality=Modality.INFERRED)
    if not rows:
        return None
    latest = max(rows, key=lambda row: row.valid_from)
    return 1.0 if latest.confidence >= 0.5 else 0.0


def score_due(
    records: Sequence[ForecastRecord], *, store: AssertionStore, at: datetime
) -> tuple[list[MetricSample], list[ForecastRecord]]:
    """期限が来た予測を採点して指標にする。

    実体・フルーエント・WM 版ごとに、先行時間別の CRPS と被覆率を出す。実測が 1 点も得られない予測は
    採点せず、戻りの「採点した予測」に含めない（次の周期で再び試す）。

    Args:
        records: 採点待ちの予測。
        store: アサーションストア（実測を引く）。
        at: 測定時刻。

    Returns:
        ``(指標, 採点した予測)``。
    """
    groups: dict[tuple[str, str, str], list[tuple[Forecast, dict[float, float], ForecastRecord]]]
    groups = defaultdict(list)
    for record in records:
        forecast = Forecast.model_validate(record.payload)
        actual: dict[float, float] = {}
        for point in forecast.points:
            outcome = realised_outcome(
                store,
                entity=forecast.entity,
                fluent=forecast.fluent,
                at=point.at(forecast.horizon_origin),
            )
            if outcome is not None:
                actual[point.lead_time_s] = outcome
        if actual:
            groups[(forecast.entity, forecast.fluent, forecast.wm_version)].append(
                (forecast, actual, record)
            )
    samples: list[MetricSample] = []
    scored: list[ForecastRecord] = []
    for (entity, fluent, wm_version), members in groups.items():
        report = score_forecasts([m[0] for m in members], observations=[m[1] for m in members])
        for lead in report.leads():
            labels = {
                "entity": entity,
                "fluent": fluent_name(fluent),
                "wm_version": wm_version,
                "lead_time_s": f"{lead:g}",
            }
            samples.append(
                MetricSample(metric("forecast_crps"), report.crps_by_lead[lead], at, labels)
            )
            samples.append(
                MetricSample(metric("forecast_coverage"), report.coverage_by_lead[lead], at, labels)
            )
        scored.extend(m[2] for m in members)
    log.info("reasoning.forecasts_scored_due", due=len(records), scored=len(scored))
    return samples, scored
