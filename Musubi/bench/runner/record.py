"""RunRecord — the unit the scoreboard ingests (shared by the relocate runner and flagships)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RunRecord:
    scenario: str
    experiment: str | None
    arm: str
    seed: int
    oracle_passed: bool
    success: bool
    unapproved_irreversible: int
    trace_completeness: float
    claims: int
    bus_events: int
    api_calls: int
    plan_steps: int
    executed: int
    reason: str
    checks: dict[str, bool] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("metrics", None)  # metrics are flagship-specific; not a scoreboard column
        return data
