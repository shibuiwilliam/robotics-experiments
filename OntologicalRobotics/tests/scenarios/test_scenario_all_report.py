"""scenario-all 恒久サマリ（R-3e）のテスト。

最新 results.json の集約・Markdown 整形を決定的に検証する（ファイルI/Oは tmp_path）。
"""

from __future__ import annotations

import json

from orx.exp import scenario as scn


# 集計対象になるための最小 provenance スタンプ（D-4: 欠落 run は legacy として除外される）
_STAMP = {"git_commit": "abc1234", "model_snapshot": "deterministic", "config_hash": "deadbeef"}


def _write_result(runs_root, name: str, data: dict) -> None:
    d = runs_root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "results.json").write_text(json.dumps(data), encoding="utf-8")


def test_latest_picks_most_recent_per_scenario(tmp_path) -> None:
    import os
    import time

    runs = tmp_path
    base = {"scenario": "s1", "conditions": ["OR-full"], **_STAMP}
    _write_result(runs, "scenario-s1-aaa-1", {**base, "seeds": [1]})
    time.sleep(0.01)
    _write_result(runs, "scenario-s1-bbb-2", {**base, "seeds": [1, 2]})
    # 明示的に mtime を進める（決定性のため）
    os.utime(runs / "scenario-s1-bbb-2" / "results.json", (time.time() + 10, time.time() + 10))
    latest = scn.latest_scenario_results(runs)
    assert set(latest) == {"s1"}
    assert latest["s1"]["seeds"] == [1, 2]  # 新しい方


def test_render_scenario_all_summarizes_falsification_and_comparisons() -> None:
    results = {
        "s1": {
            "scenario": "s1",
            "conditions": ["OR-full", "B0"],
            "seeds": [1, 2, 3],
            "falsification": {"a": True, "b": True},
            "comparisons": [{"condition_a": "OR-full", "condition_b": "B0"}],
            "git_commit": "abc123-dirty",
            "model_snapshot": "deterministic (no LLM)",
            "per_condition": {"OR-full": {"completion": 1.0}, "B0": {"completion": 0.5}},
        },
    }
    md = scn.render_scenario_all(results, "2026-06-15")
    assert "Scenario-All Summary — 2026-06-15" in md
    assert "2/2 ✓" in md  # 反証集計
    assert "abc123-dirty" in md  # スタンプ（dirty 反映）
    assert "completion" in md  # 条件別指標


def test_write_scenario_all_returns_none_when_empty(tmp_path) -> None:
    assert scn.write_scenario_all(tmp_path, tmp_path / "reports", "2026-06-15") is None


def test_comparison_digest_significant_and_deterministic() -> None:
    """R-1B: comparisons 有りはダイジェスト、無し(S5)は理由付きで『なし』。"""
    with_cmp = {
        "comparisons": [
            {
                "condition_a": "OR-full",
                "condition_b": "B1",
                "mcnemar_p": 7.81e-3,
                "wilcoxon_metric": "overall_accuracy",
                "wilcoxon_p": 7.81e-3,
            },
            {
                "condition_a": "OR-full",
                "condition_b": "round-robin",
                "mcnemar_p": 1e-3,
                "wilcoxon_metric": "overall_accuracy",
                "wilcoxon_p": 2e-3,
            },
        ]
    }
    digest = scn._comparison_digest(with_cmp)
    assert "OR-full vs {B1, round-robin}" in digest
    assert "n=2" in digest
    assert "McNemar p≤7.81e-03" in digest  # 最大(=最も弱い)p を代表に
    assert "Wilcoxon(overall_accuracy)" in digest
    assert "なし" in scn._comparison_digest({"comparisons": []})  # S5 型


def test_robustness_digest_shows_knob_endpoints() -> None:
    """R-1B: primary 条件の曲線端点 (knob 最初→最後) を 1 行に。"""
    d = {
        "conditions": ["OR-full", "B1"],
        "robustness": {
            "knob": "fault_degradation",
            "values": [0.8, 0.1],
            "overall_accuracy": {"OR-full": [0.72, 0.93], "B1": [0.33, 0.33]},
        },
    }
    lines = scn._robustness_digest(d)
    assert lines and "fault_degradation" in lines[0]
    assert "0.720→0.930" in lines[0]


def test_s7_safety_note_surfaces_zero_misdelivery() -> None:
    """R-1B: S7 は誤配送0維持 + success 曲線を明示し『成功率低=弱い』の誤読を防ぐ。"""
    d = {
        "scenario": "s7",
        "robustness": {
            "knob": "lookalike_sep",
            "values": [1.5, 0.3],
            "misdelivery_rate": {"OR-full": [0.0, 0.0]},
            "success_rate": {"OR-full": [1.0, 0.042]},
        },
    }
    note = "\n".join(scn._s7_safety_note(d))
    assert "誤配送率 0.000" in note
    assert "1.000→0.042" in note
    assert scn._s7_safety_note({"scenario": "s3"}) == []  # S7 以外は出さない


def test_render_scenario_all_includes_digests() -> None:
    results = {
        "s7": {
            "scenario": "s7",
            "conditions": ["OR-full", "OR-vec"],
            "seeds": [1, 2],
            "falsification": {"a": True},
            "comparisons": [
                {
                    "condition_a": "OR-full",
                    "condition_b": "OR-vec",
                    "mcnemar_p": 7.81e-3,
                    "wilcoxon_metric": "success_rate",
                    "wilcoxon_p": 3.12e-2,
                }
            ],
            "per_condition": {
                "OR-full": {"success_rate": 0.35, "misdelivery_rate": 0.0},
                "OR-vec": {"success_rate": 0.17, "misdelivery_rate": 0.83},
            },
            "robustness": {
                "knob": "lookalike_sep",
                "values": [1.5, 0.3],
                "misdelivery_rate": {"OR-full": [0.0, 0.0]},
                "success_rate": {"OR-full": [1.0, 0.04]},
            },
        },
    }
    md = scn.render_scenario_all(results, "2026-06-17")
    assert "対比較: OR-full vs {OR-vec}" in md
    assert "頑健性: `lookalike_sep`" in md
    assert "安全 vs 自動化" in md


def test_timestamped_suffix_avoids_same_day_overwrite(tmp_path) -> None:
    """R-C: 時刻サフィックス付きは日付のみと別ファイルになり同日再実行で上書きしない。"""
    runs = tmp_path / "runs"
    base = {
        "scenario": "s1",
        "conditions": ["OR-full"],
        "seeds": [1],
        "falsification": {"a": True},
        **_STAMP,
    }
    _write_result(runs, "scenario-s1-aaa-1", base)
    reports = tmp_path / "reports"
    plain = scn.write_scenario_all(runs, reports, "2026-06-16")
    stamped = scn.write_scenario_all(runs, reports, "2026-06-16", "06-39-53")
    assert plain is not None and stamped is not None
    assert plain.name == "scenario-all-2026-06-16.md"
    assert stamped.name == "scenario-all-2026-06-16T06-39-53.md"
    assert plain != stamped  # 同日でも上書きされない
    assert "2026-06-16T06-39-53" in stamped.read_text(encoding="utf-8")  # 見出しにも時刻
