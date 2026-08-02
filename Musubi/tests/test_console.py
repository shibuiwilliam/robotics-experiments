"""Musubi Console tests — status/providers/scenarios/run/inspect, JSON output, invariants."""

from __future__ import annotations

import json

from console.app import main


def _json_out(capsys, argv: list[str]) -> object:  # type: ignore[no-untyped-def]
    assert main(argv) == 0
    return json.loads(capsys.readouterr().out)


def test_status_reports_offline_ready_and_clean_choke_point(capsys) -> None:  # type: ignore[no-untyped-def]
    data = _json_out(capsys, ["--json", "status"])
    assert data["offline_ready"] is True
    assert data["invariants"]["no_llm_import_outside_clients"] is True
    assert data["llm_provider"] == "claude"
    assert "e0_smoke" in data["scenarios"]


def test_providers_shows_active_claude(capsys) -> None:  # type: ignore[no-untyped-def]
    data = _json_out(capsys, ["providers", "--json"])
    assert data["active"] == "claude"
    assert data["providers"]["claude"]["model"] == "claude-opus-4-8"
    assert data["providers"]["gemini"]["model"] == "gemini-3.5-flash"


def test_scenarios_ls_and_show(capsys) -> None:  # type: ignore[no-untyped-def]
    rows = _json_out(capsys, ["scenarios", "ls", "--json"])
    assert any(r["scenario"] == "f2_recall" for r in rows)
    show = _json_out(capsys, ["scenarios", "show", "f2_recall", "--json"])
    assert show["driver"] == "recall"
    assert "no_unapproved_irreversible" in show["oracle"]["must"]


def test_ontology_concepts(capsys) -> None:  # type: ignore[no-untyped-def]
    data = _json_out(capsys, ["ontology", "concepts", "--json"])
    assert "Claim" in data["concepts"] and data["count"] >= 30


def test_run_scenario_offline(capsys) -> None:  # type: ignore[no-untyped-def]
    data = _json_out(capsys, ["run", "scenario", "e0_smoke", "--arm", "A4", "--json"])
    totals = data["totals"]
    assert totals["oracle_pass"] == totals["runs"]
    assert totals["unapproved_irreversible"] == 0 and totals["api_calls"] == 0


def test_inspect_surfaces_oracle_and_drilldown(capsys) -> None:  # type: ignore[no-untyped-def]
    data = _json_out(capsys, ["inspect", "f1_confidence", "--arm", "A4", "--seed", "0", "--json"])
    assert data["success"] is True
    assert data["drilldown"]  # observation->binding->mediation chain present
    chain = next(iter(data["drilldown"].values()))
    assert len(chain) == 3
