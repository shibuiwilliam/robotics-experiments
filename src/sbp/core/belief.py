"""信念（belief）— 較正済みの確信度（設計書 §3.5、付録 A.1）。

``belief`` には**較正済みの値だけ**を入れる。生スコアは ``evidence`` に残す（不変条件 7）。
3 種の形を判別可能 union として持つ。

- :class:`BooleanBelief` — 成立確率 p ∈ [0, 1]
- :class:`CategoricalBelief` — カテゴリ上の確率質量関数
- :class:`NumericBelief` — 平均・分散（任意で分位点）

認識的不確実性（モデルの知識不足。追加観測で減る）と偶然的不確実性（観測固有のばらつき）は
分けて保持する。エピステミック・スケジューラは前者のみを対象にする（設計書 §5.6）。
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "Belief",
    "BooleanBelief",
    "CategoricalBelief",
    "NumericBelief",
    "Uncertainty",
]

_PROBABILITY_SUM_TOLERANCE = 1e-6


class Uncertainty(BaseModel):
    """不確実性の分解（設計書 §5.6）。

    Attributes:
        epistemic: 認識的不確実性。アンサンブル分散など。追加観測で減りうる。
        aleatoric: 偶然的不確実性。観測固有のばらつき。追加観測では減らない。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    epistemic: float = Field(ge=0.0)
    aleatoric: float = Field(ge=0.0)

    @property
    def total(self) -> float:
        """合計不確実性（分散の加法性を仮定した単純和）。"""
        return self.epistemic + self.aleatoric


class _BeliefBase(BaseModel):
    """信念の共通部分。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calibrated: Literal[True] = True
    """較正済みであることの明示。較正前の値を ``belief`` に入れない（不変条件 7）。"""

    uncertainty: Uncertainty | None = None
    """不確実性の分解（任意。プローブ出力では必須にする）。"""


class BooleanBelief(_BeliefBase):
    """真偽フルーエントの信念。

    Attributes:
        p: 成立確率。
    """

    kind: Literal["boolean"] = "boolean"
    p: float = Field(ge=0.0, le=1.0)

    @property
    def log_odds(self) -> float:
        """対数オッズ。``p`` が 0 または 1 のとき ``±inf``。"""
        if self.p <= 0.0:
            return -math.inf
        if self.p >= 1.0:
            return math.inf
        return math.log(self.p / (1.0 - self.p))

    @property
    def entropy_nats(self) -> float:
        """二値エントロピー（nat）。スケジューラの情報利得推定に使う。"""
        if self.p in (0.0, 1.0):
            return 0.0
        q = 1.0 - self.p
        return -(self.p * math.log(self.p) + q * math.log(q))

    def negate(self) -> BooleanBelief:
        """補事象の信念を返す。"""
        return BooleanBelief(p=1.0 - self.p, uncertainty=self.uncertainty)


class CategoricalBelief(_BeliefBase):
    """カテゴリ値フルーエントの信念。

    Attributes:
        pmf: カテゴリ（IRI またはローカル名）から確率への写像。総和は 1。
    """

    kind: Literal["categorical"] = "categorical"
    pmf: dict[str, float] = Field(min_length=1)

    @model_validator(mode="after")
    def _normalized(self) -> Self:
        """確率が [0,1] に収まり総和が 1 であることを検証する。"""
        for key, value in self.pmf.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"pmf[{key!r}]={value} is outside [0, 1]")
        total = math.fsum(self.pmf.values())
        if abs(total - 1.0) > _PROBABILITY_SUM_TOLERANCE:
            raise ValueError(f"pmf must sum to 1.0, got {total}")
        return self

    @property
    def argmax(self) -> str:
        """最大確率のカテゴリ。"""
        return max(self.pmf.items(), key=lambda kv: kv[1])[0]

    @property
    def entropy_nats(self) -> float:
        """エントロピー（nat）。"""
        return -math.fsum(p * math.log(p) for p in self.pmf.values() if p > 0.0)


class NumericBelief(_BeliefBase):
    """数値フルーエントの信念。

    Attributes:
        mean: 平均。
        var: 分散。
        quantiles: 分位点（``"0.05"`` のような文字列キー → 値）。conformal 予測区間を載せる。
        unit: QUDT の単位 IRI（任意だが物理量では付けることを推奨）。
    """

    kind: Literal["numeric"] = "numeric"
    mean: float
    var: float = Field(ge=0.0)
    quantiles: dict[str, float] | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def _quantile_keys_are_probabilities(self) -> Self:
        """分位点のキーが (0,1) の確率として解釈できることを検証する。"""
        if self.quantiles is None:
            return self
        for key in self.quantiles:
            try:
                q = float(key)
            except ValueError as exc:
                raise ValueError(f"quantile key must be a float string, got {key!r}") from exc
            if not 0.0 < q < 1.0:
                raise ValueError(f"quantile key must be in (0, 1), got {key!r}")
        return self

    @property
    def std(self) -> float:
        """標準偏差。"""
        return math.sqrt(self.var)


Belief = Annotated[
    BooleanBelief | CategoricalBelief | NumericBelief,
    Field(discriminator="kind"),
]
"""信念の判別可能 union。``kind`` で判別する（付録 A.1 の ``belief``）。"""
