"""不一致の深刻度モデル（設計書 §8.2）。

``severity = f(業務価値 v(x), 確信度, 持続時間, 規範重み)``。

**なぜ業務価値を外から持ち込むのか。** 確信度と持続時間だけで並べると「確実に起きている些末な
食い違い」が「あいまいだが高価な資産の食い違い」より上に来る。優先順位は業務の損失で決まる
のであって、推定の確からしさで決まるのではない。そのため v(x) は ObjectType ごとの設定として
``value/*.yaml`` に外出しし、コードに埋めない（閾値はコードに書かない、CLAUDE.md の規約）。

深刻度は**順位付けのための量**であって確率ではない。``belief`` には入れない（不変条件 7）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbp.core.errors import ReasoningError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from datetime import timedelta

__all__ = ["BusinessValue", "BusinessValueRegistry", "Severity", "compute_severity"]

log = get_logger(__name__)

_WEIGHT_VALUE = 0.4
"""深刻度に占める業務価値の重み。"""
_WEIGHT_CONFIDENCE = 0.3
"""深刻度に占める確信度の重み。"""
_WEIGHT_PERSISTENCE = 0.2
"""深刻度に占める持続時間の重み。"""
_WEIGHT_NORM = 0.1
"""深刻度に占める規範重みの重み（類型 5 以外では 0 になる）。"""


class BusinessValue(BaseModel):
    """ObjectType ごとの業務価値 v(x)（設計書 §8.2）。

    Attributes:
        object_type: 対象の型 IRI（CURIE で宣言し IRI に展開される）。
        asset_value: 資産価値（通貨単位。相対順位のみ使う）。
        safety_class: 安全等級（0.0 影響なし 〜 1.0 人身に関わる）。
        sla_penalty: SLA 違反時の想定ペナルティ（通貨単位）。
        note: この値の根拠（ヒアリング元など）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_type: str
    asset_value: float = Field(default=0.0, ge=0.0)
    safety_class: float = Field(default=0.0, ge=0.0, le=1.0)
    sla_penalty: float = Field(default=0.0, ge=0.0)
    note: str = ""

    @model_validator(mode="after")
    def _expand_iris(self) -> Self:
        """CURIE を IRI に展開する。"""
        from sbp.integration.mappings import expand_curie  # noqa: PLC0415 — 循環 import 回避

        object.__setattr__(self, "object_type", expand_curie(self.object_type))
        return self

    def value_at_stake(self) -> float:
        """この型の実体 1 件で失われうる業務価値。

        安全等級は金額に換算できないため、資産価値と SLA ペナルティの和を安全等級で増幅する形で
        入れる（安全に関わるものは同額でも上位に来るべきである）。
        """
        return (self.asset_value + self.sla_penalty) * (1.0 + self.safety_class)


class BusinessValueRegistry:
    """業務価値の一覧（``value/*.yaml`` から読む）。"""

    def __init__(self, values: dict[str, BusinessValue], *, default_scale: float) -> None:
        """Args:
        values: 型 IRI → 業務価値。
        default_scale: 正規化の基準額（この額を 1.0 とみなす）。
        """
        self._values = values
        self._default_scale = default_scale

    @classmethod
    def from_directory(cls, directory: Path, *, default_scale: float) -> BusinessValueRegistry:
        """ディレクトリ内の全定義を読み込む。

        Args:
            directory: ``value/`` のパス。
            default_scale: 正規化の基準額。

        Raises:
            ReasoningError: YAML が壊れている、鍵が重複している、または定義が無い場合。
        """
        values: dict[str, BusinessValue] = {}
        for path in sorted(directory.glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            for entry in payload.get("values", []):
                try:
                    value = BusinessValue.model_validate(entry)
                except Exception as exc:
                    raise ReasoningError(f"invalid business value in {path.name}: {exc}") from exc
                if value.object_type in values:
                    raise ReasoningError(
                        f"duplicate business value for {value.object_type} in {path.name}"
                    )
                values[value.object_type] = value
        if not values:
            raise ReasoningError(f"no business values found under {directory}")
        log.debug("reconciliation.values_loaded", count=len(values))
        return cls(values, default_scale=default_scale)

    def get(self, object_type: str) -> BusinessValue:
        """型の業務価値を返す。

        未登録の型は「価値ゼロ」ではなく基準額の半分を返す。価値ゼロにすると未登録の型の
        不一致が常に最下位に沈み、設定漏れが「深刻でない」として黙殺されるためである。

        Args:
            object_type: 型 IRI。
        """
        known = self._values.get(object_type)
        if known is not None:
            return known
        log.info("reconciliation.value_not_registered", object_type=object_type)
        return BusinessValue(
            object_type=object_type,
            asset_value=self._default_scale / 2.0,
            note="未登録（既定値）。value/*.yaml に追加すること",
        )

    @property
    def scale(self) -> float:
        """正規化の基準額。"""
        return self._default_scale

    def keys(self) -> tuple[str, ...]:
        """登録済みの型 IRI。"""
        return tuple(sorted(self._values))


class Severity(BaseModel):
    """不一致の深刻度（付録 A.2 の ``severity``）。

    Attributes:
        score: 0〜1 の総合スコア（順位付け用）。
        value_at_stake: 失われうる業務価値。
        confidence: 判定の確信度（区間内の平均事後など）。
        persistence_s: 条件が続いた秒数。
        norm_weight: 規範違反の重み（類型 5 以外は 0）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    value_at_stake: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    persistence_s: float = Field(ge=0.0)
    norm_weight: float = Field(default=0.0, ge=0.0, le=1.0)


def compute_severity(
    *,
    value: BusinessValue,
    confidence: float,
    persistence: timedelta,
    value_scale: float,
    persistence_scale: timedelta,
    norm_weight: float = 0.0,
) -> Severity:
    """深刻度を計算する。

    各項を 0〜1 に正規化した加重和。価値と持続時間は上限で飽和させる（1 日続いた不一致と
    1 週間続いた不一致の差は、5 分と 1 時間の差より小さい）。

    Args:
        value: 対象型の業務価値。
        confidence: 判定の確信度。
        persistence: 条件が続いた時間。
        value_scale: 価値の正規化基準（この額で 1.0）。
        persistence_scale: 持続時間の正規化基準（この長さで 1.0）。
        norm_weight: 規範違反の重み（類型 5 のみ）。

    Raises:
        ReasoningError: 正規化基準が非正の場合。
    """
    if value_scale <= 0.0:
        raise ReasoningError(f"value_scale must be positive, got {value_scale}")
    if persistence_scale.total_seconds() <= 0.0:
        raise ReasoningError(f"persistence_scale must be positive, got {persistence_scale}")
    if not 0.0 <= confidence <= 1.0:
        raise ReasoningError(f"confidence must be a probability, got {confidence}")

    at_stake = value.value_at_stake()
    value_term = min(1.0, at_stake / value_scale)
    persistence_term = min(
        1.0, max(persistence.total_seconds(), 0.0) / persistence_scale.total_seconds()
    )
    score = (
        _WEIGHT_VALUE * value_term
        + _WEIGHT_CONFIDENCE * confidence
        + _WEIGHT_PERSISTENCE * persistence_term
        + _WEIGHT_NORM * norm_weight
    )
    return Severity(
        score=min(1.0, max(0.0, score)),
        value_at_stake=at_stake,
        confidence=confidence,
        persistence_s=max(persistence.total_seconds(), 0.0),
        norm_weight=norm_weight,
    )
