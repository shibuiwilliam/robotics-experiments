"""可観測性の指標の単体テスト（設計書 §12.3、M7-03）。

重点:

1. **未定義の指標名を使えない** — 同じものが 2 系列になってダッシュボードで突き合わせられない。
2. **単位が型にある** — 「レイテンシ 300」が秒かミリ秒かでグラフの意味が変わる。
3. **割合は 0〜1** — パーセントで記録しない（Prometheus の規約）。
4. **HELP と TYPE を必ず出す** — 無いとダッシュボードを作る人が推測することになる。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sbp.core.errors import ConfigError
from sbp.core.metrics import (
    METRICS,
    MetricDomain,
    MetricSample,
    MetricUnit,
    metric,
    prometheus_exposition,
)

T0 = datetime(2026, 10, 3, 10, tzinfo=UTC)


class TestMetricCatalogue:
    def test_all_six_domains_are_covered(self) -> None:
        """設計書 §12.3 の 6 領域すべてに指標があること。"""
        domains = {definition.domain for definition in METRICS.values()}
        assert domains == set(MetricDomain)

    def test_an_unknown_metric_is_refused(self) -> None:
        """自由な文字列を許すと probe_auroc と auroc_probe の 2 系列ができる。"""
        with pytest.raises(ConfigError, match="unknown metric"):
            metric("auroc_probe")

    def test_the_error_lists_what_exists(self) -> None:
        with pytest.raises(ConfigError, match="known:"):
            metric("nonexistent")

    def test_every_metric_has_a_description(self) -> None:
        """説明が無い指標は、ダッシュボードで何を見ているか分からない。"""
        for definition in METRICS.values():
            assert definition.description

    def test_the_design_document_metrics_are_present(self) -> None:
        """設計書 §12.3 の表に挙がっている指標が揃っていること。"""
        for key in (
            "latent_shift",
            "pred_error",
            "align_residual",
            "probe_auroc",
            "probe_ece",
            "lifting_delay",
            "identity_revoke_rate",
            "update_latency",
            "contrib_observed",
            "discrepancies_raised",
            "resolution_time",
            "suppression_rate",
            "learning_yield",
            "cdc_lag",
            "mapping_violations",
            "event_log_lag",
            "regrounding_duration",
        ):
            assert metric(key).key == key


class TestPrometheusNaming:
    def test_the_unit_is_in_the_name(self) -> None:
        """Prometheus の命名規約。グラフを見た人が単位を推測しなくてよい。"""
        assert metric("probe_auroc").prometheus_name.endswith("_ratio")
        assert metric("update_latency").prometheus_name.endswith("_seconds")
        assert metric("discrepancies_raised").prometheus_name.endswith("_total")
        assert metric("belief_entropy").prometheus_name.endswith("_bits")

    def test_the_domain_is_in_the_name(self) -> None:
        assert metric("probe_auroc").prometheus_name.startswith("sbp_grounding_")
        assert metric("latent_shift").prometheus_name.startswith("sbp_worldmodel_")

    def test_counts_use_the_total_suffix(self) -> None:
        assert MetricUnit.COUNT.suffix == "_total"


class TestMetricSample:
    def test_a_ratio_outside_the_unit_interval_is_refused(self) -> None:
        """パーセントで記録すると、グラフの軸が 100 倍ずれる。"""
        with pytest.raises(ConfigError, match="is a ratio but the value"):
            MetricSample(name=metric("probe_auroc"), value=99.95, at=T0, labels={})

    def test_a_negative_duration_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="cannot be negative"):
            MetricSample(name=metric("update_latency"), value=-1.0, at=T0, labels={})

    def test_entropy_may_exceed_one(self) -> None:
        """bit 単位の指標に 0〜1 の制約を掛けない（多値のエントロピーは 1 を超える）。"""
        sample = MetricSample(name=metric("belief_entropy"), value=2.5, at=T0, labels={})
        assert sample.value == pytest.approx(2.5)

    def test_the_row_carries_the_domain_and_unit(self) -> None:
        row = MetricSample(
            name=metric("probe_auroc"), value=0.99, at=T0, labels={"probe": "running"}
        ).as_row()
        assert row["name"] == "probe_auroc"
        assert row["domain"] == "grounding"
        assert row["unit"] == "ratio"
        assert row["probe"] == "running"


class TestExposition:
    def _samples(self) -> list[MetricSample]:
        return [
            MetricSample(
                name=metric("probe_auroc"),
                value=0.9995,
                at=T0,
                labels={"probe": "running", "wm_version": "koopman/1.0.0"},
            ),
            MetricSample(name=metric("discrepancies_raised"), value=3, at=T0, labels={"kind": "2"}),
        ]

    def test_help_and_type_are_emitted(self) -> None:
        """無くても取り込めるが、見る人が「この系列は何か」を推測することになる。"""
        text = prometheus_exposition(self._samples())
        assert "# HELP sbp_grounding_probe_auroc_ratio" in text
        assert "# TYPE sbp_grounding_probe_auroc_ratio gauge" in text
        assert "# TYPE sbp_reconciliation_discrepancies_raised_total counter" in text

    def test_labels_are_rendered_in_a_stable_order(self) -> None:
        """順序が揺れると差分が読めない。"""
        text = prometheus_exposition(self._samples())
        assert 'probe="running",wm_version="koopman/1.0.0"' in text

    def test_a_sample_without_labels_has_no_braces(self) -> None:
        text = prometheus_exposition(
            [MetricSample(name=metric("event_log_lag"), value=0.5, at=T0, labels={})]
        )
        assert "sbp_operations_event_log_lag_seconds 0.5" in text

    def test_label_values_are_escaped(self) -> None:
        text = prometheus_exposition(
            [
                MetricSample(
                    name=metric("event_log_lag"),
                    value=0.5,
                    at=T0,
                    labels={"note": 'quote " and \\ backslash'},
                )
            ]
        )
        assert '\\"' in text
        assert "\\\\" in text

    def test_samples_of_the_same_series_are_grouped(self) -> None:
        samples = [
            MetricSample(name=metric("probe_auroc"), value=0.99, at=T0, labels={"probe": "a"}),
            MetricSample(name=metric("probe_auroc"), value=0.98, at=T0, labels={"probe": "b"}),
        ]
        text = prometheus_exposition(samples)
        assert text.count("# HELP sbp_grounding_probe_auroc_ratio") == 1
        assert text.count("sbp_grounding_probe_auroc_ratio{") == 2
