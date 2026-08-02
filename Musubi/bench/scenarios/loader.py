"""Scenario DSL loader (SCENARIOS.md §2).

Parses `bench/scenarios/*.yaml` into a :class:`Scenario`, validated against the committed DSL JSON
Schema (`dsl.schema.json`) so unknown keys fail. Most sections are optional — a scenario opts into
the one extensible DSL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from bench.oracle import OracleSpec

_SCENARIO_DIR = Path(__file__).resolve().parent
_SCHEMA_PATH = _SCENARIO_DIR / "dsl.schema.json"


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Scenario:
    name: str
    oracle: OracleSpec
    title: str = ""
    description: str = ""
    experiment: str | None = None
    driver: str | None = None
    arms: list[str] = field(default_factory=lambda: ["A4"])
    seeds: list[int] = field(default_factory=lambda: [0])
    perception: str = "oracle"
    planner: dict[str, str] = field(default_factory=dict)
    goal: dict[str, Any] = field(default_factory=dict)
    world: dict[str, Any] = field(default_factory=dict)
    entities: list[dict[str, Any]] = field(default_factory=list)
    ledger: dict[str, Any] = field(default_factory=dict)
    external: dict[str, Any] = field(default_factory=dict)
    documents: list[dict[str, Any]] = field(default_factory=list)
    norms_active: list[str] = field(default_factory=list)
    norms: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    invisible_hand: list[dict[str, Any]] = field(default_factory=list)
    faults: list[dict[str, Any]] = field(default_factory=list)
    injections: list[dict[str, Any]] = field(default_factory=list)
    perturbations: list[dict[str, Any]] = field(default_factory=list)
    regime: dict[str, Any] | None = None
    agents_external: list[Any] = field(default_factory=list)
    human_proxy: dict[str, Any] | None = None
    sweep: dict[str, Any] | None = None
    ground_truth: dict[str, Any] = field(default_factory=dict)

    def planner_for(self, arm: str) -> str:
        """The planner backend for an arm (A0/A1 scripted; A2–A4 gemini), overridable per-arm."""
        if arm in self.planner:
            return self.planner[arm]
        return "scripted" if arm in ("A0", "A1") else "gemini"

    def driver_name(self) -> str:
        if self.driver:
            return self.driver
        if self.goal.get("type"):
            return str(self.goal["type"])
        return self.name

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Scenario:
        seeds = data.get("seeds")
        if seeds is None:
            base = int(data.get("seed", 0))
            seeds = [base + i for i in range(int(data.get("repeats", 1)))]
        return Scenario(
            name=str(data["scenario"]),
            oracle=OracleSpec.from_dict(data.get("oracle")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            experiment=data.get("experiment"),
            driver=data.get("driver"),
            arms=list(data.get("arms", ["A4"])),
            seeds=list(seeds),
            perception=str(data.get("perception", "oracle")),
            planner=dict(data.get("planner", {})),
            goal=dict(data.get("goal", {})),
            world=dict(data.get("world", {})),
            entities=list(data.get("entities", [])),
            ledger=dict(data.get("ledger", {})),
            external=dict(data.get("external", {})),
            documents=list(data.get("documents", [])),
            norms_active=list(data.get("norms_active", [])),
            norms=list(data.get("norms", [])),
            orders=list(data.get("orders", [])),
            tasks=list(data.get("tasks", [])),
            invisible_hand=list(data.get("invisible_hand", [])),
            faults=list(data.get("faults", [])),
            injections=list(data.get("injections", [])),
            perturbations=list(data.get("perturbations", [])),
            regime=data.get("regime"),
            agents_external=list(data.get("agents_external", [])),
            human_proxy=data.get("human_proxy"),
            sweep=data.get("sweep"),
            ground_truth=dict(data.get("ground_truth", {})),
        )


def validate_dsl(data: dict[str, Any]) -> None:
    """Validate a scenario dict against the DSL JSON Schema (unknown keys fail)."""
    import jsonschema

    jsonschema.validate(data, _schema())


def scenario_path(name: str) -> Path:
    p = name if name.endswith(".yaml") else f"{name}.yaml"
    return _SCENARIO_DIR / p


def load_scenario(name: str) -> Scenario:
    path = scenario_path(name)
    if not path.exists():
        raise FileNotFoundError(f"scenario not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_dsl(data)
    return Scenario.from_dict(data)


def list_scenarios() -> list[str]:
    return sorted(p.stem for p in _SCENARIO_DIR.glob("*.yaml"))
