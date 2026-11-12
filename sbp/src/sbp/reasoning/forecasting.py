"""予測の生成・書き出し・採点（M9-02、ADR-0032）。

``sbp.reasoning.prediction`` は型と採点の関数を持つが、予測を**作る**者がいなかった。ここが
それである: WM のロールアウト（潜在の標本軌道）にプローブを通し、フルーエントの成立確率の
分布を先行時間ごとに作る。

- **確率はプローブの較正済み出力**（不変条件 7）。軌道の標本ごとに確率を出す。平均が予測確率、
  標本の分位点が**確率の認識的な幅**（区間）。
- **予測は ``predicted``**（不変条件 3）。主信念には入らない。起点（``horizon_origin``）違いで
  共存する。
- **採点は二値事象の作法で行う。** 実測は 0 / 1 で、予測は確率 p。CRPS は Bernoulli(p) に対する
  ``(p − y)²``（Brier と一致する適正スコア）。被覆率は**予測集合**で測る: 名目 ``level`` に対して
  「確率が level 以上の側だけ」「どちらも届かなければ両方」を集合とし、実測が集合に入った割合。
  較正されていれば名目以上になる。標本の分布そのものを CRPS に渡すと、認識的な広がりが
  「鋭さ」として報われ、区間は 0 / 1 に届かず被覆率が 0 に張り付く。
- **採点の「実測」は belief-worker の信念の事後**（0.5 以上なら 1）。真値ではない——運用には
  真値が無い。信念が誤っていれば採点も誤る。物理に対する精度ではなく、基盤の信念との整合である
  （ADR-0032 の限界。真値は ``sbp.testing`` 以外から読めない: 不変条件 10）。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

import numpy as np

from sbp.bridge.runner import fluent_name
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger
from sbp.core.metrics import MetricSample, metric
from sbp.core.modality import Modality
from sbp.reasoning.prediction import Forecast, ForecastPoint

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime
    from uuid import UUID

    from numpy.typing import NDArray

    from sbp.knowledge.assertions import AssertionStore
    from sbp.knowledge.forecasts import ForecastRecord
    from sbp.worldmodel.base import LatentState, Trajectory

__all__ = [
    "Rollouter",
    "forecast_assertions",
    "make_forecast",
    "missing_assertions",
    "prediction_set",
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
    forecast_id: UUID | None = None,
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
        forecast_id: 識別子（再実行で同じ予測が同じ ID になるよう、呼び出し側が決定的に決める）。

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
        forecast_id=forecast_id or uuid4(),
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


def missing_assertions(
    forecast: Forecast, *, store: AssertionStore, tx_from: datetime
) -> list[Assertion]:
    """まだ書かれていない ``predicted`` のアサーション（再実行・中断後の補完）。

    予測の識別子が決定的なら来歴も決定的なので、来歴と ``valid_from`` で書き済みの点を除ける。

    Args:
        forecast: 予測。
        store: アサーションストア。
        tx_from: 記録時刻。
    """
    existing = {
        row.valid_from
        for row in store.query(
            subject=forecast.entity, predicate=forecast.fluent, modality=Modality.PREDICTED
        )
        if row.provenance == f"urn:sbp:prov:forecast/{forecast.forecast_id}"
    }
    return [
        assertion
        for assertion in forecast_assertions(forecast, tx_from=tx_from)
        if assertion.valid_from not in existing
    ]


def realised_outcome(
    store: AssertionStore,
    *,
    entity: str,
    fluent: str,
    at: datetime,
    belief_provenance: str = "urn:sbp:prov:belief-worker",
) -> float | None:
    """その時刻の信念（belief-worker の最新の事後）から 0 / 1 の実測を作る。無ければ ``None``。

    **belief-worker が書いた文だけを見る**（プローブ・規則・LLM の ``inferred`` や結合の文を
    実測にしない）。現在の ``tx`` で引くので、後から訂正された信念で過去の予測を採点しうる
    （訂正を反映するほうが実測として正しい、と判断している。ADR-0032）。

    Args:
        store: アサーションストア。
        entity: 実体 IRI。
        fluent: フルーエント型 IRI。
        at: 対象の時刻。
        belief_provenance: 信念更新の来歴の接頭辞。
    """
    rows = [
        row
        for row in store.query(
            subject=entity, predicate=fluent, valid_at=at, modality=Modality.INFERRED
        )
        if _is_belief(row.provenance, belief_provenance)
    ]
    if not rows:
        return None
    latest = max(rows, key=lambda row: (row.valid_from, row.tx_from, row.assertion_id.int))
    return 1.0 if latest.confidence >= 0.5 else 0.0


def _is_belief(provenance: str, prefix: str) -> bool:
    rest = provenance[len(prefix) :]
    return (
        provenance.startswith(prefix) and rest.startswith("/") and not rest.startswith("/coupling/")
    )


def prediction_set(probability: float, level: float) -> frozenset[int]:
    """名目 ``level`` の予測集合（確率が level 以上になる最小の集合）。

    Args:
        probability: 成立確率 p。
        level: 名目被覆。
    """
    if probability >= level:
        return frozenset({1})
    if 1.0 - probability >= level:
        return frozenset({0})
    return frozenset({0, 1})


def score_due(
    records: Sequence[ForecastRecord],
    *,
    store: AssertionStore,
    at: datetime,
    complete_wait: timedelta = timedelta(0),
) -> tuple[list[MetricSample], list[ForecastRecord]]:
    """期限が来た予測を採点して指標にする。

    全先行時間の実測（信念）が揃った予測だけを採点する。揃わない予測は、地平の終端から
    ``complete_wait`` を過ぎるまで次の周期に回す（遅れた信念の到着を待つ。過ぎたら揃った点だけ
    で採点する）。実体・フルーエント・WM 版・先行時間ごとに、この周期で採点した予測から
    CRPS（Brier）と予測集合の被覆率を出す。

    Args:
        records: 採点待ちの予測。
        store: アサーションストア（実測を引く）。
        at: 測定時刻。
        complete_wait: 全点が揃うのを待つ上限。

    Returns:
        ``(指標, 採点した予測)``。
    """
    crps: dict[tuple[str, str, str, float], list[float]] = defaultdict(list)
    covered: dict[tuple[str, str, str, float], list[float]] = defaultdict(list)
    scored: list[ForecastRecord] = []
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
        complete = len(actual) == len(forecast.points)
        if not actual or not (complete or at - record.horizon_end >= complete_wait):
            continue
        for point in forecast.points:
            if point.lead_time_s not in actual:
                continue
            outcome = actual[point.lead_time_s]
            key = (forecast.entity, forecast.fluent, forecast.wm_version, point.lead_time_s)
            crps[key].append((point.mean - outcome) ** 2)
            covered[key].append(
                1.0 if int(outcome) in prediction_set(point.mean, forecast.interval_level) else 0.0
            )
        scored.append(record)
    samples: list[MetricSample] = []
    for (entity, fluent, wm_version, lead), values in crps.items():
        labels = {
            "entity": entity,
            "fluent": fluent_name(fluent),
            "wm_version": wm_version,
            "lead_time_s": repr(lead),
        }
        samples.append(MetricSample(metric("forecast_crps"), float(np.mean(values)), at, labels))
        samples.append(
            MetricSample(
                metric("forecast_coverage"),
                float(np.mean(covered[(entity, fluent, wm_version, lead)])),
                at,
                labels,
            )
        )
    log.info("reasoning.forecasts_scored_due", due=len(records), scored=len(scored))
    return samples, scored
