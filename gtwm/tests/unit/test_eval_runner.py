"""`eval/runner.py`：config→実行→runs/<exp>/<ts>/ 書き出しの検査。"""

from __future__ import annotations

import json

import pytest
from omegaconf import OmegaConf

from gtwm.eval.runner import generate_report, judge_criteria, run

pytestmark = pytest.mark.unit


def _fake_measure(config, seed):  # noqa: ANN001
    return {"position_fact_f1": 0.5 + 0.1 * seed, "type_accuracy": 0.9}


def test_run_writes_all_expected_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("gtwm.eval.runner.repo_root", lambda: tmp_path)
    monkeypatch.setattr("gtwm.eval.runner._write_mlflow", lambda *a, **k: None)
    config = OmegaConf.create({"smoke": True, "seeds": [0]})

    result = run("EXP-TEST", config, _fake_measure, run_timestamp="20260101-0000")

    assert result.run_dir == tmp_path / "runs" / "EXP-TEST" / "20260101-0000"
    for fname in ["metrics.json", "config_resolved.yaml", "git.txt", "log.txt"]:
        assert (result.run_dir / fname).exists()

    payload = json.loads((result.run_dir / "metrics.json").read_text())
    assert payload["exp_id"] == "EXP-TEST"
    assert payload["smoke"] is True
    assert payload["aggregated"]["position_fact_f1"] == pytest.approx(0.5)


def test_run_aggregates_across_seeds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("gtwm.eval.runner.repo_root", lambda: tmp_path)
    monkeypatch.setattr("gtwm.eval.runner._write_mlflow", lambda *a, **k: None)
    config = OmegaConf.create({"smoke": False, "seeds": [0, 1, 2]})

    result = run("EXP-TEST", config, _fake_measure, run_timestamp="20260101-0001")
    # position_fact_f1 の値は 0.5, 0.6, 0.7 -> 平均 0.6
    assert result.metrics["aggregated"]["position_fact_f1"] == pytest.approx(0.6)


def test_judge_criteria_pass_and_fail() -> None:
    criteria = {"position_fact_f1": {"target": 0.9, "op": ">="}}
    assert judge_criteria({"position_fact_f1": 0.95}, criteria) == "合格"
    assert judge_criteria({"position_fact_f1": 0.5}, criteria) == "不合格"
    assert judge_criteria({}, criteria) == "不合格"  # 指標が無い


def test_judge_criteria_skips_report_only() -> None:
    criteria = {"monthly_cost_per_zone": {"target": None, "op": "report_only"}}
    assert judge_criteria({}, criteria) == "合格"


def test_generate_report_smoke_never_claims_pass_fail(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("gtwm.eval.runner.repo_root", lambda: tmp_path)
    monkeypatch.setattr("gtwm.eval.runner._write_mlflow", lambda *a, **k: None)
    config = OmegaConf.create({"smoke": True, "seeds": [0]})
    result = run("EXP-TEST", config, _fake_measure, run_timestamp="20260101-0002")

    criteria = {"position_fact_f1": {"target": 0.9, "op": ">="}}
    config_path = "experiments/EXP-TEST/config.yaml"
    report = generate_report(
        "EXP-TEST", "テスト目的", config_path, result, criteria, "本実行には...が必要"
    )
    assert "## 判定\n**参考（smoke）**" in report
    assert "**合格**" not in report
    assert "**不合格**" not in report


def test_generate_report_full_run_judges_pass_fail(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("gtwm.eval.runner.repo_root", lambda: tmp_path)
    monkeypatch.setattr("gtwm.eval.runner._write_mlflow", lambda *a, **k: None)
    config = OmegaConf.create({"smoke": False, "seeds": [0]})
    result = run("EXP-TEST", config, _fake_measure, run_timestamp="20260101-0003")

    criteria = {"position_fact_f1": {"target": 0.9, "op": ">="}}
    report = generate_report(
        "EXP-TEST", "テスト目的", "experiments/EXP-TEST/config.yaml", result, criteria, ""
    )
    assert "**不合格**" in report
