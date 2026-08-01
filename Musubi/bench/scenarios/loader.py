"""Scenario DSL loader (PROJECT.md §8: Scenario DSL).

A scenario declares, in YAML: the initial goal, the Invisible Hand schedule (ledger/reality
divergence), active norms, the perception dial, the ablation arms, seeds/repeats, and the oracle
checks. Scenarios are the benchmark canon (committed, seed-reproducible).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_SCENARIO_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Scenario:
    name: str
    goal: dict[str, Any]
    arms: list[str] = field(default_factory=lambda: ["A4"])
    seeds: list[int] = field(default_factory=lambda: [0])
    dial: str = "oracle"
    invisible_hand: list[dict[str, Any]] = field(default_factory=list)
    norms: list[dict[str, Any]] = field(default_factory=list)
    oracle: list[str] = field(
        default_factory=lambda: ["relocate_reached", "no_unapproved_irreversible"]
    )
    experiment: str | None = None
    description: str = ""

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Scenario:
        seeds = data.get("seeds")
        if seeds is None:
            repeats = int(data.get("repeats", 1))
            base = int(data.get("seed", 0))
            seeds = [base + i for i in range(repeats)]
        return Scenario(
            name=str(data["name"]),
            goal=dict(data["goal"]),
            arms=list(data.get("arms", ["A4"])),
            seeds=list(seeds),
            dial=str(data.get("dial", "oracle")),
            invisible_hand=list(data.get("invisible_hand", [])),
            norms=list(data.get("norms", [])),
            oracle=list(data.get("oracle", ["relocate_reached", "no_unapproved_irreversible"])),
            experiment=data.get("experiment"),
            description=str(data.get("description", "")),
        )


def scenario_path(name: str) -> Path:
    p = name if name.endswith(".yaml") else f"{name}.yaml"
    return _SCENARIO_DIR / p


def load_scenario(name: str) -> Scenario:
    path = scenario_path(name)
    if not path.exists():
        raise FileNotFoundError(f"scenario not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Scenario.from_dict(data)


def list_scenarios() -> list[str]:
    return sorted(p.stem for p in _SCENARIO_DIR.glob("*.yaml"))
