"""予測サービス（設計書 §8.3、M5-05）。

> 出力：SOSA Forecast として格納（horizon_origin、lead_time、conditionedOn）。
> 精度追跡：起点別に CRPS / 区間被覆率を記録し、地平ごとの「信頼できる先行時間」を公開する。

**起点（``horizon_origin``）を必ず持つ。** 「10:00 に点検済みになる」という予測は、09:00 に出した
ものと 09:55 に出したもので価値が全く違う。起点を落とすと予測の良し悪しを測れなくなり、
「信頼できる先行時間」（何分先までなら当たるか）が出せない。

**先行時間ごとに精度を追跡する。** 全体の平均 CRPS は意味がない——短期は当たり長期は外れるのが
普通で、平均はその境目を隠す。設計書 §8.3 が「地平ごとの信頼できる先行時間を公開する」と
言っているのは、使う側が「この予測は信じてよいか」を判断できるようにするためである。

**被覆率は較正の指標であって精度の指標ではない。** 区間を広く取れば被覆率は 1 に近づく（が
役に立たない）。CRPS（鋭さと当たりの両方を見る）と**並べて**記録する。片方だけでは
「広くて当たる予測」と「狭くて当たる予測」を区別できない。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Self
from uuid import UUID, uuid4

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger
from sbp.core.time import UtcDatetime, ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy.typing import NDArray

__all__ = [
    "AccuracyReport",
    "Forecast",
    "ForecastPoint",
    "continuous_ranked_probability_score",
    "interval_coverage",
    "reliable_lead_time",
    "score_forecasts",
]

log = get_logger(__name__)

_DEFAULT_INTERVAL = 0.9
"""既定の予測区間（中央 90%）。"""


def continuous_ranked_probability_score(samples: NDArray[np.float64], observed: float) -> float:
    """標本で表した予測分布の CRPS（設計書 §8.3 の精度追跡）。

    経験分布に対する CRPS を定義式から計算する::

        CRPS = E|X − y| − ½·E|X − X'|

    第 1 項は当たり（観測への近さ）、第 2 項は鋭さ（分布の広がり）。**広い分布は第 2 項で罰
    される**ので、「区間を広げて当てる」ことができない。ここが被覆率との違いである。

    Args:
        samples: 予測分布の標本。
        observed: 観測値。

    Returns:
        CRPS（小さいほど良い。0 は完全予測）。

    Raises:
        ReasoningError: 標本が空の場合。
    """
    if samples.size == 0:
        raise ReasoningError("CRPS needs at least one sample")
    accuracy = float(np.mean(np.abs(samples - observed)))
    spread = float(np.mean(np.abs(samples[:, None] - samples[None, :])))
    return accuracy - 0.5 * spread


def conformal_halfwidth(residuals: Sequence[float], *, level: float) -> float:
    """残差から予測区間の半幅を較正する（conformal、設計書 §8.5）。

    **固定幅の区間は状態が変わる時点で必ず外れる。** 持続予測の区間幅を手で決めると、遷移を
    またぐ地平で被覆率が名目を下回る——そして「幅を広げて通す」のは較正ではなく調整である。
    conformal 予測は較正データの残差分位点を半幅にするので、**交換可能性の仮定の下で有限標本でも
    名目被覆を保証する**。手で決める余地が無いのが利点である。

    有限標本補正に ``ceil((n+1)·level) / n`` 番目の分位点を使う（単純に ``level`` 分位点を取ると
    名目をわずかに下回る）。

    Args:
        residuals: 較正データでの絶対残差。
        level: 名目被覆（0.9 なら 90%）。

    Returns:
        区間の半幅。

    Raises:
        ReasoningError: 残差が空、または ``level`` が範囲外の場合。
    """
    if not residuals:
        raise ReasoningError(
            "conformal calibration needs residuals from a calibration split; without them the "
            "interval width would be a guess"
        )
    if not 0.0 < level < 1.0:
        raise ReasoningError(f"level must be in (0, 1), got {level}")
    ordered = np.sort(np.abs(np.asarray(residuals, dtype=np.float64)))
    count = ordered.size
    index = int(np.ceil((count + 1) * level)) - 1
    return float(ordered[min(index, count - 1)])


def interval_coverage(
    observations: Sequence[float], lowers: Sequence[float], uppers: Sequence[float]
) -> float:
    """予測区間の被覆率。

    Args:
        observations: 観測値の列。
        lowers: 区間の下端の列。
        uppers: 区間の上端の列。

    Returns:
        区間に入った割合（0〜1）。

    Raises:
        ReasoningError: 長さが合わない、または空の場合。
    """
    if not observations:
        raise ReasoningError("coverage needs at least one observation")
    if not len(observations) == len(lowers) == len(uppers):
        raise ReasoningError(
            f"coverage needs matching lengths, got {len(observations)} observations, "
            f"{len(lowers)} lowers, {len(uppers)} uppers"
        )
    covered = sum(
        1
        for observed, lower, upper in zip(observations, lowers, uppers, strict=True)
        if lower <= observed <= upper
    )
    return covered / len(observations)


class ForecastPoint(BaseModel):
    """ある先行時間の予測 1 点。

    Attributes:
        lead_time_s: 起点からの先行時間（秒）。
        mean: 予測の平均。
        lower: 予測区間の下端。
        upper: 予測区間の上端。
        samples: 分布の標本（CRPS の計算に使う）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lead_time_s: float = Field(gt=0.0)
    mean: float = Field(ge=0.0, le=1.0)
    lower: float = Field(ge=0.0, le=1.0)
    upper: float = Field(ge=0.0, le=1.0)
    samples: tuple[float, ...] = ()

    @model_validator(mode="after")
    def _interval_is_ordered_and_contains_the_mean(self) -> Self:
        """区間が逆順でなく平均を含むことを検証する。"""
        if self.upper < self.lower:
            raise ReasoningError(
                f"forecast interval at lead {self.lead_time_s}s is inverted "
                f"[{self.lower}, {self.upper}]"
            )
        if not self.lower <= self.mean <= self.upper:
            raise ReasoningError(
                f"forecast mean {self.mean} at lead {self.lead_time_s}s is outside its own "
                f"interval [{self.lower}, {self.upper}]"
            )
        return self

    @property
    def width(self) -> float:
        """区間の幅（鋭さの指標）。"""
        return self.upper - self.lower

    def at(self, origin: datetime) -> datetime:
        """この点が対象とする時刻。

        Args:
            origin: 予測の起点。
        """
        return ensure_utc(origin) + timedelta(seconds=self.lead_time_s)


class Forecast(BaseModel):
    """SOSA Forecast（設計書 §8.3）。

    Attributes:
        forecast_id: 識別子。
        entity: 対象実体の IRI。
        fluent: 対象フルーエント型 IRI。
        horizon_origin: 予測を出した時刻。**必須**。
        points: 先行時間ごとの予測（昇順）。
        conditioned_on: 条件付けに使ったものの IRI（``PlanItem``、信念、WM 版）。
        wm_version: 予測を出した WM の版 IRI。
        interval_level: 予測区間の名目被覆（0.9 なら中央 90%）。
        ontology_version: 生成時のオントロジー版。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    forecast_id: UUID = Field(default_factory=uuid4)
    entity: str
    fluent: str
    horizon_origin: UtcDatetime
    points: tuple[ForecastPoint, ...] = Field(min_length=1)
    conditioned_on: tuple[str, ...] = ()
    wm_version: str
    interval_level: float = Field(default=_DEFAULT_INTERVAL, gt=0.0, lt=1.0)
    ontology_version: str

    @model_validator(mode="after")
    def _leads_increase(self) -> Self:
        """先行時間が昇順で重複が無いことを検証する。"""
        leads = [point.lead_time_s for point in self.points]
        if leads != sorted(set(leads)):
            raise ReasoningError(
                f"forecast for {self.entity} has non-increasing or duplicated lead times: {leads}"
            )
        if not self.wm_version:
            raise ReasoningError(
                "a forecast must name the WM version that produced it; otherwise its accuracy "
                "cannot be attributed to a model (design §4.8)"
            )
        return self

    @property
    def horizon_s(self) -> float:
        """最も遠い先行時間（秒）。"""
        return self.points[-1].lead_time_s

    def point_at(self, lead_time_s: float) -> ForecastPoint:
        """先行時間で点を引く。

        Args:
            lead_time_s: 先行時間（秒）。

        Raises:
            ReasoningError: その先行時間の点が無い場合。
        """
        for point in self.points:
            if point.lead_time_s == lead_time_s:
                return point
        raise ReasoningError(
            f"this forecast has no point at lead {lead_time_s}s; available: "
            f"{[point.lead_time_s for point in self.points]}"
        )

    def needs_refresh(
        self,
        *,
        plan_changed: Sequence[str],
    ) -> bool:
        """計画が変わって再予測が要るか（設計書 §8.3「計画変更時は再予測をトリガ」）。

        Args:
            plan_changed: 変更された ``PlanItem`` の IRI。
        """
        return bool(set(plan_changed) & set(self.conditioned_on))


@dataclass(frozen=True, slots=True)
class AccuracyReport:
    """起点別・先行時間別の精度（設計書 §8.3、``metrics`` 表に入れる）。

    Attributes:
        entity: 対象実体の IRI。
        fluent: 対象フルーエント型 IRI。
        wm_version: 評価対象の WM 版。
        crps_by_lead: 先行時間（秒）→ CRPS。
        coverage_by_lead: 先行時間（秒）→ 被覆率。
        width_by_lead: 先行時間（秒）→ 区間幅の平均。
        samples_by_lead: 先行時間（秒）→ 評価に使った予測の件数。
        interval_level: 名目被覆。
    """

    entity: str
    fluent: str
    wm_version: str
    crps_by_lead: dict[float, float]
    coverage_by_lead: dict[float, float]
    width_by_lead: dict[float, float]
    samples_by_lead: dict[float, int]
    interval_level: float

    def leads(self) -> tuple[float, ...]:
        """評価した先行時間（昇順）。"""
        return tuple(sorted(self.crps_by_lead))

    def is_calibrated_at(self, lead_time_s: float, *, tolerance: float = 0.05) -> bool:
        """その先行時間で被覆率が名目どおりか。

        Args:
            lead_time_s: 先行時間（秒）。
            tolerance: 許容する下振れ。

        Raises:
            ReasoningError: その先行時間を評価していない場合。
        """
        if lead_time_s not in self.coverage_by_lead:
            raise ReasoningError(f"lead {lead_time_s}s was not evaluated")
        return self.coverage_by_lead[lead_time_s] >= self.interval_level - tolerance

    def as_metric_rows(self, *, at: datetime) -> tuple[dict[str, object], ...]:
        """``metrics`` 表に入れる行（設計書 §12.3）。

        Args:
            at: 記録時刻。
        """
        moment = ensure_utc(at)
        rows: list[dict[str, object]] = []
        for lead in self.leads():
            for name, value in (
                ("forecast_crps", self.crps_by_lead[lead]),
                ("forecast_coverage", self.coverage_by_lead[lead]),
                ("forecast_interval_width", self.width_by_lead[lead]),
            ):
                rows.append(
                    {
                        "ts": moment,
                        "name": name,
                        "value": value,
                        "entity": self.entity,
                        "fluent": self.fluent,
                        "wm_version": self.wm_version,
                        "lead_time_s": lead,
                        "n": self.samples_by_lead[lead],
                    }
                )
        return tuple(rows)


def score_forecasts(
    forecasts: Sequence[Forecast],
    *,
    observations: Sequence[dict[float, float]],
) -> AccuracyReport:
    """起点別の予測を先行時間ごとに採点する（設計書 §8.3）。

    **先行時間ごとに分けて集計する。** 全体平均の CRPS は短期の当たりで長期の外れを隠す。

    Args:
        forecasts: 同じ実体・フルーエントに対する予測の列（起点が違う）。
        observations: 各予測に対応する ``先行時間 → 実測値``。

    Returns:
        先行時間別の精度。

    Raises:
        ReasoningError: 予測が空、長さが合わない、対象が揃っていない場合。
    """
    if not forecasts:
        raise ReasoningError("scoring needs at least one forecast")
    if len(forecasts) != len(observations):
        raise ReasoningError(f"{len(forecasts)} forecasts but {len(observations)} observation sets")
    entities = {forecast.entity for forecast in forecasts}
    fluents = {forecast.fluent for forecast in forecasts}
    versions = {forecast.wm_version for forecast in forecasts}
    if len(entities) > 1 or len(fluents) > 1:
        raise ReasoningError(
            "scoring mixes entities or fluents; accuracy is tracked per entity and fluent "
            f"(entities={sorted(entities)}, fluents={sorted(fluents)})"
        )
    if len(versions) > 1:
        raise ReasoningError(
            f"scoring mixes WM versions {sorted(versions)}; accuracy must be attributable to one "
            "model (design §4.8)"
        )

    crps: dict[float, list[float]] = {}
    covered: dict[float, list[float]] = {}
    widths: dict[float, list[float]] = {}

    for forecast, actual in zip(forecasts, observations, strict=True):
        for point in forecast.points:
            if point.lead_time_s not in actual:
                continue
            observed = actual[point.lead_time_s]
            samples = (
                np.asarray(point.samples, dtype=np.float64)
                if point.samples
                # 標本が無い予測は平均 1 点の退化した分布として扱う（鋭さは 0 になる）
                else np.asarray([point.mean], dtype=np.float64)
            )
            crps.setdefault(point.lead_time_s, []).append(
                continuous_ranked_probability_score(samples, observed)
            )
            covered.setdefault(point.lead_time_s, []).append(
                1.0 if point.lower <= observed <= point.upper else 0.0
            )
            widths.setdefault(point.lead_time_s, []).append(point.width)

    if not crps:
        raise ReasoningError(
            "no forecast point had a matching observation; check that the observation keys are "
            "lead times in seconds"
        )

    report = AccuracyReport(
        entity=next(iter(entities)),
        fluent=next(iter(fluents)),
        wm_version=next(iter(versions)),
        crps_by_lead={lead: float(np.mean(values)) for lead, values in crps.items()},
        coverage_by_lead={lead: float(np.mean(values)) for lead, values in covered.items()},
        width_by_lead={lead: float(np.mean(values)) for lead, values in widths.items()},
        samples_by_lead={lead: len(values) for lead, values in crps.items()},
        interval_level=forecasts[0].interval_level,
    )
    log.info(
        "reasoning.forecasts_scored",
        entity=report.entity,
        fluent=report.fluent,
        wm_version=report.wm_version,
        leads=len(report.crps_by_lead),
        forecasts=len(forecasts),
    )
    return report


def reliable_lead_time(
    report: AccuracyReport, *, max_crps: float, tolerance: float = 0.05
) -> float | None:
    """「信頼できる先行時間」を返す（設計書 §8.3）。

    CRPS が閾値以下で、かつ被覆率が名目どおりである**最長の**先行時間。途中で条件を満たさなく
    なったらそこで打ち切る——先行時間が伸びて一度崩れた予測が、さらに先で偶然当たっても
    「そこまで信頼できる」とは言えない。

    Args:
        report: 精度報告。
        max_crps: 許容する CRPS。
        tolerance: 被覆率の許容下振れ。

    Returns:
        信頼できる最長の先行時間（秒）。1 点も満たさなければ ``None``。
    """
    reliable: float | None = None
    for lead in report.leads():
        if report.crps_by_lead[lead] > max_crps or not report.is_calibrated_at(
            lead, tolerance=tolerance
        ):
            break
        reliable = lead
    return reliable
