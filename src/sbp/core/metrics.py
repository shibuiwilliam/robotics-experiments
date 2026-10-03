"""可観測性の指標（設計書 §12.3、M7-03）。

設計書 §12.3 の 6 領域を型で固定する。

| 領域 | 指標 |
|---|---|
| WM | 潜在シフト、予測誤差、整合残差、推論レイテンシ |
| 接地 | プローブ AUROC/ECE、事象昇格の遅延、同一性束縛率・撤回率 |
| 信念 | 更新レイテンシ、三源寄与率、エントロピー分布 |
| 照合 | 起票数/類型、解決時間、抑制率、学習還元率 |
| 統合 | CDC 遅延、写像不変条件違反、信頼性モデルの更新頻度 |
| 運用 | イベントログ滞留、再接地の所要時間 |

**指標名を列挙にするのが要点である。** 自由な文字列にすると、同じものが
``probe_auroc`` と ``auroc_probe`` の 2 つの系列になり、ダッシュボードで突き合わせられない。

**単位を型に持たせる。** 「レイテンシ 300」が秒かミリ秒かでグラフの意味が変わる。実測値を
記録するときに単位を宣言させ、Prometheus のメトリクス名にも単位を含める（``_seconds``,
``_ratio``）——これは Prometheus の命名規約でもある。

**閾値はここに持たない。** 判定は ``Settings`` が持ち（CLAUDE.md の定数の置き場）、この層は
「測った値」だけを扱う。指標と閾値を同じ場所に置くと、閾値の変更が過去の記録の意味を変える。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sbp.core.errors import ConfigError
from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "METRICS",
    "MetricDomain",
    "MetricName",
    "MetricSample",
    "MetricUnit",
    "metric",
    "prometheus_exposition",
]

log = get_logger(__name__)


class MetricDomain(StrEnum):
    """指標の領域（設計書 §12.3 の表の行）。"""

    WORLD_MODEL = "worldmodel"
    GROUNDING = "grounding"
    BELIEF = "belief"
    RECONCILIATION = "reconciliation"
    INTEGRATION = "integration"
    OPERATIONS = "operations"


class MetricUnit(StrEnum):
    """単位。Prometheus の命名規約に合わせた接尾辞を持つ。"""

    SECONDS = "seconds"
    """時間。ミリ秒ではなく秒で記録する（Prometheus の規約）。"""
    RATIO = "ratio"
    """0〜1 の割合。パーセントにしない（規約）。"""
    COUNT = "count"
    """件数。"""
    BITS = "bits"
    """情報量（エントロピー）。"""

    @property
    def suffix(self) -> str:
        """Prometheus のメトリクス名に付ける接尾辞。"""
        return {"seconds": "_seconds", "ratio": "_ratio", "count": "_total", "bits": "_bits"}[
            self.value
        ]


@dataclass(frozen=True, slots=True)
class MetricName:
    """指標 1 つの定義。

    Attributes:
        key: 系列名（``metrics`` テーブルの ``name`` 列に入る）。
        domain: 領域。
        unit: 単位。
        description: 何を測っているか（ダッシュボードの説明に出す）。
    """

    key: str
    domain: MetricDomain
    unit: MetricUnit
    description: str

    @property
    def prometheus_name(self) -> str:
        """Prometheus のメトリクス名（``sbp_`` 接頭辞 + 単位の接尾辞）。"""
        base = f"sbp_{self.domain.value}_{self.key}"
        return base if base.endswith(self.unit.suffix) else base + self.unit.suffix


# 設計書 §12.3 の 6 領域。**新しい指標はここに足してから使う**（自由な文字列を禁止する）。
METRICS: dict[str, MetricName] = {
    metric.key: metric
    for metric in (
        # ---- WM ----
        MetricName(
            "latent_shift", MetricDomain.WORLD_MODEL, MetricUnit.RATIO, "潜在分布のシフト（MMD）"
        ),
        MetricName(
            "pred_error", MetricDomain.WORLD_MODEL, MetricUnit.RATIO, "1 刻み予測の相対誤差"
        ),
        MetricName(
            "align_residual",
            MetricDomain.WORLD_MODEL,
            MetricUnit.RATIO,
            "整合残差 ‖A(f(z,a)) − T(A(z),a)‖",
        ),
        MetricName(
            "inference_latency", MetricDomain.WORLD_MODEL, MetricUnit.SECONDS, "推論レイテンシ"
        ),
        # ---- 接地 ----
        MetricName("probe_auroc", MetricDomain.GROUNDING, MetricUnit.RATIO, "プローブの AUROC"),
        MetricName("probe_ece", MetricDomain.GROUNDING, MetricUnit.RATIO, "プローブの ECE"),
        MetricName("lifting_delay", MetricDomain.GROUNDING, MetricUnit.SECONDS, "事象昇格の遅延"),
        MetricName("identity_bind_rate", MetricDomain.GROUNDING, MetricUnit.RATIO, "同一性束縛率"),
        MetricName(
            "identity_revoke_rate", MetricDomain.GROUNDING, MetricUnit.RATIO, "同一性撤回率"
        ),
        # ---- 信念 ----
        MetricName(
            "update_latency", MetricDomain.BELIEF, MetricUnit.SECONDS, "信念更新のレイテンシ"
        ),
        MetricName(
            "contrib_observed", MetricDomain.BELIEF, MetricUnit.RATIO, "三源寄与率のうち観測の割合"
        ),
        MetricName("contrib_record", MetricDomain.BELIEF, MetricUnit.RATIO, "同 記録の割合"),
        MetricName("contrib_plan", MetricDomain.BELIEF, MetricUnit.RATIO, "同 計画の割合"),
        MetricName("belief_entropy", MetricDomain.BELIEF, MetricUnit.BITS, "信念のエントロピー"),
        # ---- 照合 ----
        MetricName("discrepancies_raised", MetricDomain.RECONCILIATION, MetricUnit.COUNT, "起票数"),
        MetricName(
            "resolution_time",
            MetricDomain.RECONCILIATION,
            MetricUnit.SECONDS,
            "起票から解決までの時間",
        ),
        MetricName("suppression_rate", MetricDomain.RECONCILIATION, MetricUnit.RATIO, "抑制率"),
        MetricName(
            "learning_yield",
            MetricDomain.RECONCILIATION,
            MetricUnit.RATIO,
            "学習還元率（解決のうち学習信号になった割合）",
        ),
        # ---- 統合 ----
        MetricName(
            "cdc_lag",
            MetricDomain.INTEGRATION,
            MetricUnit.SECONDS,
            "業務システムからの取り込み遅延",
        ),
        MetricName(
            "mapping_violations", MetricDomain.INTEGRATION, MetricUnit.COUNT, "写像の不変条件違反"
        ),
        MetricName(
            "reliability_updates",
            MetricDomain.INTEGRATION,
            MetricUnit.COUNT,
            "信頼性モデルの更新回数",
        ),
        # ---- 運用 ----
        MetricName(
            "event_log_lag", MetricDomain.OPERATIONS, MetricUnit.SECONDS, "イベントログの滞留"
        ),
        MetricName(
            "regrounding_duration", MetricDomain.OPERATIONS, MetricUnit.SECONDS, "再接地の所要時間"
        ),
        MetricName(
            "forecast_coverage", MetricDomain.OPERATIONS, MetricUnit.RATIO, "予測区間の被覆率"
        ),
        MetricName("forecast_crps", MetricDomain.OPERATIONS, MetricUnit.RATIO, "予測の CRPS"),
    )
}
"""設計書 §12.3 の指標。自由な文字列ではなくここを引く。"""


def metric(key: str) -> MetricName:
    """指標を名前で引く。

    Args:
        key: 系列名。

    Raises:
        ConfigError: 未定義の指標。**ここで落とすのが要点である**——自由な文字列を許すと、
            同じものが 2 つの系列になってダッシュボードで突き合わせられない。
    """
    try:
        return METRICS[key]
    except KeyError as exc:
        raise ConfigError(
            f"unknown metric {key!r}; declare it in sbp.core.metrics.METRICS first "
            f"(known: {sorted(METRICS)})"
        ) from exc


@dataclass(frozen=True, slots=True)
class MetricSample:
    """実測値 1 件（``metrics`` テーブルの 1 行）。

    Attributes:
        name: 指標。
        value: 値。
        at: 測定時刻。
        labels: 絞り込みの軸（``entity`` / ``wm_version`` / ``kind`` など）。
    """

    name: MetricName
    value: float
    at: datetime
    labels: dict[str, str]

    def __post_init__(self) -> None:
        """値域と時刻を検証する。"""
        object.__setattr__(self, "at", ensure_utc(self.at))
        if self.name.unit is MetricUnit.RATIO and not 0.0 <= self.value <= 1.0:
            raise ConfigError(
                f"{self.name.key} is a ratio but the value is {self.value}; record ratios in "
                "[0, 1] (not percentages — Prometheus convention)"
            )
        if self.name.unit in {MetricUnit.SECONDS, MetricUnit.COUNT} and self.value < 0.0:
            raise ConfigError(f"{self.name.key} cannot be negative, got {self.value}")

    def as_row(self) -> dict[str, object]:
        """``metrics`` テーブルに入れる行。"""
        return {
            "ts": self.at,
            "name": self.name.key,
            "value": self.value,
            "domain": self.name.domain.value,
            "unit": self.name.unit.value,
            **self.labels,
        }


def prometheus_exposition(samples: Sequence[MetricSample]) -> str:
    """Prometheus の exposition format に変換する。

    ``# HELP`` と ``# TYPE`` を必ず出す。無くても取り込めるが、**ダッシュボードを作る人が
    「この系列は何か」を推測することになる**——指標の説明を型に持たせている理由がそこで失われる。

    Args:
        samples: 実測値。

    Returns:
        exposition format の文字列。
    """
    by_name: dict[str, list[MetricSample]] = {}
    for sample in samples:
        by_name.setdefault(sample.name.prometheus_name, []).append(sample)

    lines: list[str] = []
    for prometheus_name, group in sorted(by_name.items()):
        definition = group[0].name
        kind = "counter" if definition.unit is MetricUnit.COUNT else "gauge"
        lines.append(f"# HELP {prometheus_name} {definition.description}")
        lines.append(f"# TYPE {prometheus_name} {kind}")
        for sample in group:
            labels = ",".join(
                f'{key}="{_escape(value)}"' for key, value in sorted(sample.labels.items())
            )
            suffix = f"{{{labels}}}" if labels else ""
            lines.append(f"{prometheus_name}{suffix} {sample.value}")
    log.debug("core.prometheus_exposed", series=len(by_name), samples=len(samples))
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    """ラベル値をエスケープする（Prometheus の規約）。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
