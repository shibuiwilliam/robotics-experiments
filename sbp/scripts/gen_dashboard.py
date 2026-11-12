"""``sbp.core.metrics.METRICS`` から Grafana ダッシュボードを生成する（``make gen``）。

M8-06。設計書 §12.3 の 6 領域 25 指標を、領域ごとの行（row）に並べた 1 枚のダッシュボードに
する。

**手で書かない理由。** 指標名は :mod:`sbp.core.metrics` が列挙で固定しており、ダッシュボードの
クエリはその名前を参照する。手で書くと、指標を足したときにダッシュボードが追従せず、
**「測っているのに見えない」指標が静かに増える**。生成すれば、`make gen` の差分が追従漏れを
教える（`ontology-check` の生成物差分検査と同じ仕組み）。

**1 枚にする理由。** 領域ごとに別ファイルにすると、運用で最初に見る画面が 6 つになる。
日次点検（OPS-01）は「どこかが赤いか」を 1 画面で見たい。掘るのは行を開いてからでよい。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from sbp.core.metrics import METRICS, MetricDomain, MetricName, MetricUnit

DASHBOARD_PATH = Path("infra/grafana/dashboards/sbp-overview.json")
"""生成先。``infra/grafana/provisioning/dashboards/sbp.yml`` がこのディレクトリを見る。"""

_UID = "sbp-overview"
"""ダッシュボードの UID。**固定する**——変えると既存のリンク・ブックマークが切れる。"""

_DOMAIN_TITLES: dict[MetricDomain, str] = {
    MetricDomain.WORLD_MODEL: "WM — 潜在ダイナミクスの健全性",
    MetricDomain.GROUNDING: "接地 — プローブと事象昇格",
    MetricDomain.BELIEF: "信念 — 三源統合",
    MetricDomain.RECONCILIATION: "照合 — 不一致のライフサイクル",
    MetricDomain.INTEGRATION: "統合 — 業務システム連携",
    MetricDomain.OPERATIONS: "運用 — 滞留・再接地・予測",
}
"""行の見出し。領域名だけでは運用者に何の画面か分からない。"""

_PANEL_WIDTH = 8
"""パネルの幅（24 グリッドなので 1 行に 3 枚）。"""
_PANEL_HEIGHT = 7


def _unit(unit: MetricUnit) -> str:
    """Grafana の単位識別子。

    Args:
        unit: 指標の単位。
    """
    return {
        MetricUnit.SECONDS: "s",
        MetricUnit.RATIO: "percentunit",
        MetricUnit.COUNT: "short",
        MetricUnit.BITS: "none",
    }[unit]


def _thresholds(metric: MetricName) -> dict[str, Any]:
    """閾値の色分け。

    **閾値を数値で埋め込まない。** 既定値は ``Settings`` が持ち、拠点ごとに上書きされる
    （`SBP_BRIDGE_PROMOTE_MAX_ECE` など）。ダッシュボードに焼き込むと設定と二重管理になり、
    設定を変えてもダッシュボードの色が変わらない。色分けは「値がある／無い」だけにして、
    判定は運用手順（runbook）と Settings に委ねる。

    Args:
        metric: 指標。
    """
    del metric
    return {
        "mode": "absolute",
        "steps": [{"color": "text", "value": None}],
    }


def _panel(metric: MetricName, *, panel_id: int, x: int, y: int) -> dict[str, Any]:
    """指標 1 つのパネル（時系列）。

    Args:
        metric: 指標。
        panel_id: パネル ID（ダッシュボード内で一意）。
        x: グリッドの x 座標。
        y: グリッドの y 座標。
    """
    return {
        "id": panel_id,
        "type": "timeseries",
        "title": f"{metric.key} — {metric.description}",
        "description": (
            f"{metric.description}（領域 {metric.domain.value}、単位 {metric.unit.value}）。"
            "系列名は sbp.core.metrics の列挙で固定されている。"
        ),
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "gridPos": {"h": _PANEL_HEIGHT, "w": _PANEL_WIDTH, "x": x, "y": y},
        "targets": [
            {
                "refId": "A",
                "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
                "expr": metric.prometheus_name,
                # ラベルを凡例に出す（entity / wm_version / kind で分かれる）
                "legendFormat": "{{__name__}} {{entity}}{{wm_version}}{{kind}}",
            }
        ],
        "fieldConfig": {
            "defaults": {
                "unit": _unit(metric.unit),
                "thresholds": _thresholds(metric),
                "custom": {"drawStyle": "line", "lineWidth": 1, "fillOpacity": 5},
                # **「データが無い」を 0 で埋めない。** 0 と「経路が動いていない」は違う
                # （docs/PROGRESS.md 所見 32）。無いものは線が途切れて見える
                "noValue": "no data",
            },
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
    }


def _row(domain: MetricDomain, *, panel_id: int, y: int) -> dict[str, Any]:
    """領域の行（折りたたみ可）。

    Args:
        domain: 領域。
        panel_id: パネル ID。
        y: グリッドの y 座標。
    """
    return {
        "id": panel_id,
        "type": "row",
        "title": _DOMAIN_TITLES[domain],
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "panels": [],
    }


def build() -> dict[str, Any]:
    """ダッシュボードの JSON を組み立てる。

    Returns:
        Grafana のダッシュボード定義。
    """
    panels: list[dict[str, Any]] = []
    panel_id = 1
    y = 0
    for domain in MetricDomain:
        in_domain = [metric for metric in METRICS.values() if metric.domain is domain]
        if not in_domain:
            continue
        panels.append(_row(domain, panel_id=panel_id, y=y))
        panel_id += 1
        y += 1
        for index, metric in enumerate(sorted(in_domain, key=lambda m: m.key)):
            panels.append(
                _panel(
                    metric,
                    panel_id=panel_id,
                    x=(index % 3) * _PANEL_WIDTH,
                    y=y + (index // 3) * _PANEL_HEIGHT,
                )
            )
            panel_id += 1
        y += ((len(in_domain) - 1) // 3 + 1) * _PANEL_HEIGHT

    return {
        "uid": _UID,
        "title": "SBP 概況（6 領域）",
        "description": (
            "設計書 §12.3 の指標。**生成物**（scripts/gen_dashboard.py）なので手編集しない"
            "——指標を足したら make gen で追従させる。"
        ),
        "tags": ["sbp", "generated"],
        "timezone": "utc",
        "editable": False,
        "schemaVersion": 39,
        "refresh": "1m",
        "time": {"from": "now-6h", "to": "now"},
        "templating": {
            "list": [
                {
                    "name": "DS_PROMETHEUS",
                    "type": "datasource",
                    "query": "prometheus",
                    "current": {"text": "Prometheus", "value": "Prometheus"},
                    "hide": 0,
                }
            ]
        },
        "panels": panels,
    }


def main() -> int:
    """ダッシュボードを書き出す。"""
    payload = build()
    DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    panels = sum(1 for panel in payload["panels"] if panel["type"] != "row")
    rows = sum(1 for panel in payload["panels"] if panel["type"] == "row")
    sys.stdout.write(
        f"generated {DASHBOARD_PATH} ({rows} rows, {panels} panels, {len(METRICS)} metrics)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
