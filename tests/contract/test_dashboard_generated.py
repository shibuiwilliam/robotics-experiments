"""Grafana ダッシュボードの生成物の契約テスト（M8-06）。

ダッシュボードは ``sbp.core.metrics.METRICS`` からの生成物（``scripts/gen_dashboard.py``）で、
手編集しない。ここで検査するのは

1. **コミットされている JSON が再生成と一致する**（追従漏れが `make check` で落ちる）、
2. **全指標にパネルがある**（測っているのに見えない指標が無い）、
3. **パネルの系列名が列挙に実在する**（存在しない系列を参照したパネルが無い）、
4. **UID が固定されている**（既存のリンク・ブックマークが切れない）。

M7-03 の「指標名を列挙で固定したので、ダッシュボードを後から作っても系列名がずれない」という
主張を、ここで機械検査に落としている。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.gen_dashboard import DASHBOARD_PATH, build

from sbp.core.metrics import METRICS

REPO_ROOT = Path(__file__).resolve().parents[2]


def _committed() -> dict[str, Any]:
    """コミットされているダッシュボード JSON。"""
    payload = json.loads((REPO_ROOT / DASHBOARD_PATH).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _panels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """行以外のパネル。"""
    panels = payload["panels"]
    assert isinstance(panels, list)
    return [panel for panel in panels if panel.get("type") != "row"]


def test_the_committed_dashboard_matches_regeneration() -> None:
    """再生成しても同じ内容になる。

    落ちたら ``make gen`` を実行してコミットする。**JSON を手で直さない**——指標を足したときに
    ダッシュボードが追従しない状態を、この検査が防いでいる。
    """
    assert _committed() == build(), "make gen を実行して差分をコミットすること"


def test_every_metric_has_a_panel() -> None:
    """全指標にパネルがある（測っているのに見えない指標が無い）。"""
    exprs = {panel["targets"][0]["expr"] for panel in _panels(_committed())}
    expected = {definition.prometheus_name for definition in METRICS.values()}

    assert exprs == expected


def test_no_panel_references_an_unknown_series() -> None:
    """パネルが参照する系列名はすべて列挙にある。

    存在しない系列のパネルは、画面上では「データが無い」に見える——指標が止まっているのと
    区別できない（所見 32）。
    """
    known = {definition.prometheus_name for definition in METRICS.values()}
    for panel in _panels(_committed()):
        assert panel["targets"][0]["expr"] in known, panel["title"]


def test_every_domain_has_a_row() -> None:
    """6 領域すべてに行がある（設計書 §12.3 の表の行に対応）。"""
    rows = [panel for panel in _committed()["panels"] if panel.get("type") == "row"]
    domains = {definition.domain for definition in METRICS.values()}

    assert len(rows) == len(domains) == 6


def test_the_uid_is_stable() -> None:
    """UID を変えない（既存のリンク・ブックマークが切れる）。"""
    assert _committed()["uid"] == "sbp-overview"


def test_panels_do_not_fill_missing_data_with_zero() -> None:
    """「データが無い」を 0 で埋めない。

    0 と「経路が動いていない」は違う（所見 32）。埋めると静かな故障が正常値に見える。
    """
    for panel in _panels(_committed()):
        defaults = panel["fieldConfig"]["defaults"]
        assert defaults["noValue"] == "no data", panel["title"]


def test_the_dashboard_is_not_editable_in_the_ui() -> None:
    """UI で編集できない（正典はリポジトリの生成物である）。"""
    assert _committed()["editable"] is False
