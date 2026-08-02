"""P7 bench + scoreboard tests — scenario DSL, run driver, oracle, metrics, report, dashboard."""

from __future__ import annotations

import pytest

from bench.runner.run import run_scenario
from bench.scenarios import list_scenarios, load_scenario
from scoreboard.metrics import MetricsStore
from scoreboard.reports.report import build_report


def test_scenario_loads() -> None:
    assert "e0_smoke" in list_scenarios()
    sc = load_scenario("e0_smoke")
    assert sc.experiment == "E0"
    assert sc.goal["to_zone"] == "shipping"
    assert set(sc.arms) == {"A0", "A1", "A2", "A3", "A4"}


def test_run_scenario_single_arm_oracle_passes() -> None:
    records = run_scenario("e0_smoke", arm="A4", seeds=[0])
    assert len(records) == 1
    r = records[0]
    assert r.oracle_passed and r.success
    assert r.unapproved_irreversible == 0
    assert r.api_calls == 0
    assert r.checks["must_ok"] and r.checks["acceptable"]


def test_metrics_store_aggregates() -> None:
    store = MetricsStore(":memory:")
    store.ingest(
        [
            _rec("A4", 0, True, 0, 1.0),
            _rec("A4", 1, True, 0, 1.0),
            _rec("A0", 0, False, 0, 0.0),
        ]
    )
    summaries = {s.arm: s for s in store.arm_summaries("e0_smoke")}
    assert summaries["A4"].oracle_pass_rate == 1.0 and summaries["A4"].n == 2
    assert summaries["A0"].oracle_pass_rate == 0.0
    store.close()


def test_report_builds_from_records(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from scoreboard.reports import report as report_mod

    monkeypatch.setattr(report_mod, "_REPORT_DIR", tmp_path)
    records = run_scenario("e0_smoke", arm="A4", seeds=[0]) + run_scenario(
        "e0_smoke", arm="A0", seeds=[0]
    )
    out = build_report("E0", records=records)
    assert out.exists()
    text = out.read_text()
    assert "Ablation ladder" in text and "Unapproved irreversible" in text


@pytest.mark.slow
def test_dashboard_builds(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from scoreboard.dashboard import build_dashboard

    out = build_dashboard("e0_smoke", out_path=tmp_path / "index.html")
    html = out.read_text()
    assert "Semantic Observability" in html
    assert "Ablation ladder" in html
    assert "Case trace" in html


def _rec(arm: str, seed: int, passed: bool, unappr: int, trace: float) -> dict[str, object]:
    return {
        "scenario": "e0_smoke",
        "experiment": "E0",
        "arm": arm,
        "seed": seed,
        "oracle_passed": passed,
        "success": passed,
        "unapproved_irreversible": unappr,
        "trace_completeness": trace,
        "claims": 10,
        "bus_events": 9,
        "api_calls": 0,
        "plan_steps": 4,
        "executed": 4,
        "reason": "ok",
    }
