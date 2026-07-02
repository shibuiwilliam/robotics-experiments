"""REPORT.md 再生成（R-INFRA）の単体テスト。

REPORT.md は git 非追跡で消失しうるため、results.json から決定的に再生成できることを保証する。
"""

from __future__ import annotations

import json
from pathlib import Path

from orx.exp.report import (
    build_main_report,
    latest_results_by_scope,
    write_main_report,
)


def _det_result(sid: str) -> dict:
    return {
        "scenario": sid,
        "conditions": ["OR-full", "B0"],
        "per_condition": {"OR-full": {"score": 1.0}, "B0": {"score": 0.0}},
        "falsification": {"p1": True, "p2": True},
        "seeds": [1, 2],
        "loop": "closed" if sid == "s8" else "open",
        "comparisons": [],
        "model_snapshot": "deterministic",
        "git_commit": "abc1234",
        "config_hash": "deadbeef",
    }


def test_report_has_all_8_scenarios_and_total() -> None:
    det = {f"s{i}": _det_result(f"s{i}") for i in range(1, 9)}
    md = build_main_report(det, {}, "2026-06-27")
    for i in range(1, 9):
        assert f"### s{i} —" in md
    assert "16/16 ✓" in md  # 8 シナリオ × 2 予言
    # s8 が agent に無い → 未計測ゲートを明示
    assert "未計測（承認ゲート K2）" in md
    assert "S8=closed" in md  # ループ種別の明示


def test_report_marks_agent_scope_present() -> None:
    det = {"s8": _det_result("s8")}
    agent = {
        "s8": {
            "scenario": "s8",
            "conditions": ["OR-full-llm", "B0-llm"],
            "falsification": {"a": True},
            "model_snapshot": "live: test (mode=cache)",
            "git_commit": "abc1234",
            "config_hash": "deadbeef",
        }
    }
    md = build_main_report(det, agent, "2026-06-27")
    assert "実測済み" in md
    assert "live: test (mode=cache)" in md


def _stamped(sid: str, scope: str) -> dict:
    return {
        "scenario": sid,
        "scope": scope,
        "git_commit": "abc1234",
        "model_snapshot": "deterministic" if scope == "deterministic" else "live: t",
        "config_hash": "deadbeef",
    }


def test_latest_results_by_scope_separates(tmp_path: Path) -> None:
    for tag, scope in (("a", "deterministic"), ("b", "agent")):
        d = tmp_path / f"scenario-s1-{tag}"
        d.mkdir()
        (d / "results.json").write_text(json.dumps(_stamped("s1", scope)), encoding="utf-8")
    det, agent = latest_results_by_scope(tmp_path)
    assert "s1" in det and "s1" in agent


def test_legacy_runs_without_provenance_are_excluded(tmp_path: Path) -> None:
    """provenance（git_commit/model_snapshot/config_hash）を欠く legacy run は
    最新選択から除外される（IMPROVEMENT.md D-4）。"""
    legacy = tmp_path / "scenario-s1-legacy"
    legacy.mkdir()
    (legacy / "results.json").write_text(
        json.dumps({"scenario": "s1", "scope": "agent"}), encoding="utf-8"
    )
    det, agent = latest_results_by_scope(tmp_path)
    assert det == {} and agent == {}

    # provenance 有りの run が並存する場合、legacy が mtime で勝っても選ばれない
    modern = tmp_path / "scenario-s1-modern"
    modern.mkdir()
    (modern / "results.json").write_text(
        json.dumps(_stamped("s1", "deterministic")), encoding="utf-8"
    )
    import os
    import time

    now = time.time()
    os.utime(legacy / "results.json", (now + 100, now + 100))  # legacy を「最新」に見せる
    det, agent = latest_results_by_scope(tmp_path)
    assert "s1" in det and det["s1"]["git_commit"] == "abc1234"
    assert agent == {}

    from orx.exp.scenario import latest_scenario_results

    latest = latest_scenario_results(tmp_path)
    assert latest["s1"]["git_commit"] == "abc1234"


def test_write_main_report_creates_nonempty_file(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    d = runs / "scenario-s8-x"
    d.mkdir(parents=True)
    (d / "results.json").write_text(json.dumps(_det_result("s8")), encoding="utf-8")
    out = write_main_report(runs, tmp_path / "REPORT.md", "2026-06-27")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert len(text) > 200
    assert "REPORT.md — ORX 検証レポート" in text
