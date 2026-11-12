"""検出規則に共通の文脈（設計書 §8.2）。

五類型の規則はそれぞれ別の証拠を見るが、**閾値・業務価値・信頼性モデル・オントロジー版**は
共通に要る。これを引数で引き回すと規則ごとに取り違えが起きるため、1 つの文脈オブジェクトに
まとめて渡す。

閾値は :class:`~sbp.core.config.ReasoningSettings` 由来で、拠点別に上書きできる
（``for_site``）。拠点ごとに記録習慣が違う——同じ θ で全拠点を見ると、記録が丁寧な拠点では
何も出ず、雑な拠点では洪水になる。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

from sbp.core.errors import ReasoningError

if TYPE_CHECKING:
    from datetime import datetime

    from sbp.core.config import ReasoningSettings
    from sbp.integration.reliability import ReliabilityRegistry
    from sbp.reasoning.reconciliation.severity import BusinessValue, BusinessValueRegistry

__all__ = ["DetectionContext", "SiteOverride"]


@dataclass(frozen=True, slots=True)
class SiteOverride:
    """拠点別の閾値上書き（設計書 §8.2「閾値 θ・持続 τ は Settings、拠点別に上書き可」）。

    Attributes:
        site: 拠点 IRI。
        theta_existence: 類型 1 の閾値（``None`` なら既定）。
        theta_state_js: 類型 2 の閾値。
        theta_norm: 類型 5 の閾値。
        persistence_s: 持続時間 τ（秒）。
    """

    site: str
    theta_existence: float | None = None
    theta_state_js: float | None = None
    theta_norm: float | None = None
    persistence_s: float | None = None


@dataclass(frozen=True, slots=True)
class DetectionContext:
    """検出規則の共通文脈。

    Attributes:
        now: 検出を走らせている時刻（``detected_at`` になる）。
        ontology_version: 検出時のオントロジー版。
        values: 業務価値のレジストリ（深刻度の v(x)）。
        reliability: 情報源信頼性モデルのレジストリ。
        entity_types: 実体 IRI → ObjectType IRI（業務価値の引き当てに使う）。
        theta_existence: 類型 1 の確率閾値 θ_1。
        theta_state_js: 類型 2 の JS 距離閾値 θ_2。
        theta_norm: 類型 5 の確率閾値 θ_5。
        time_tolerance_percentile: 類型 3 の許容パーセンタイル。
        persistence: 持続時間 τ。
        value_scale: 深刻度の価値正規化基準。
        persistence_scale: 深刻度の持続時間正規化基準。
    """

    now: datetime
    ontology_version: str
    values: BusinessValueRegistry
    reliability: ReliabilityRegistry
    entity_types: dict[str, str]
    theta_existence: float
    theta_state_js: float
    theta_norm: float
    time_tolerance_percentile: float
    persistence: timedelta
    value_scale: float
    persistence_scale: timedelta

    @classmethod
    def from_settings(
        cls,
        settings: ReasoningSettings,
        *,
        now: datetime,
        ontology_version: str,
        values: BusinessValueRegistry,
        reliability: ReliabilityRegistry,
        entity_types: dict[str, str] | None = None,
    ) -> DetectionContext:
        """設定から文脈を組み立てる。

        Args:
            settings: 推論層の設定。
            now: 検出時刻。
            ontology_version: オントロジー版。
            values: 業務価値レジストリ。
            reliability: 信頼性モデルレジストリ。
            entity_types: 実体 IRI → 型 IRI。
        """
        return cls(
            now=now,
            ontology_version=ontology_version,
            values=values,
            reliability=reliability,
            entity_types=dict(entity_types or {}),
            theta_existence=settings.theta_existence,
            theta_state_js=settings.theta_state_js,
            theta_norm=settings.theta_norm,
            time_tolerance_percentile=settings.time_tolerance_percentile,
            persistence=timedelta(seconds=settings.persistence_s),
            value_scale=settings.severity_value_scale,
            persistence_scale=timedelta(seconds=settings.severity_persistence_scale_s),
        )

    def for_site(self, override: SiteOverride) -> DetectionContext:
        """拠点別の上書きを適用した文脈を返す。

        Args:
            override: 拠点別の上書き。

        Raises:
            ReasoningError: 上書きの値が確率・正の時間でない場合。
        """
        for name, value in (
            ("theta_existence", override.theta_existence),
            ("theta_state_js", override.theta_state_js),
            ("theta_norm", override.theta_norm),
        ):
            if value is not None and not 0.0 <= value <= 1.0:
                raise ReasoningError(f"{name} override for {override.site} must be in [0, 1]")
        if override.persistence_s is not None and override.persistence_s <= 0.0:
            raise ReasoningError(f"persistence_s override for {override.site} must be positive")
        return replace(
            self,
            theta_existence=override.theta_existence or self.theta_existence,
            theta_state_js=override.theta_state_js or self.theta_state_js,
            theta_norm=override.theta_norm or self.theta_norm,
            persistence=(
                self.persistence
                if override.persistence_s is None
                else timedelta(seconds=override.persistence_s)
            ),
        )

    def value_of(self, entity: str) -> BusinessValue:
        """実体の業務価値を引き当てる。

        Args:
            entity: 実体 IRI。
        """
        object_type = self.entity_types.get(entity, entity)
        return self.values.get(object_type)
