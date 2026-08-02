"""RunContext — the four data sources an oracle may read (SCENARIOS.md §3).

Sim truth (`world` god-view), the Claim store, the event/episode log (`events`), and external mock
state (`external`). Drivers execute the scenario and record what they observed/computed into
`extras` (the episode log); predicates read these — never the system's own beliefs for scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.bus import Envelope
    from core.claimstore import ClaimStore
    from sim.world import World


@dataclass
class RunContext:
    """Everything a single (arm, seed) run exposes to the oracle."""

    arm: str
    seed: int
    world: World | None = None
    claims: ClaimStore | None = None
    events: list[Envelope] = field(default_factory=list)
    external: dict[str, Any] = field(default_factory=dict)
    ground_truth: dict[str, Any] = field(default_factory=dict)
    vars: dict[str, Any] = field(default_factory=dict)
    #: driver-computed observations (the episode log): detected sets, costs, reports, chains…
    extras: dict[str, Any] = field(default_factory=dict)
    api_calls: int = 0

    def get(self, key: str, default: Any = None) -> Any:
        return self.extras.get(key, default)
