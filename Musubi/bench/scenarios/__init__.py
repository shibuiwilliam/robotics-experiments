"""Scenario DSL — declarative benchmark specs (YAML). See loader.Scenario."""

from __future__ import annotations

from bench.scenarios.loader import Scenario, list_scenarios, load_scenario, scenario_path

__all__ = ["Scenario", "load_scenario", "list_scenarios", "scenario_path"]
