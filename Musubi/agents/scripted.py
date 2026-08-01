"""ScriptedPlanner — deterministic planning with no LLM (offline bootstrap + lower ablation arms).

Understands a small set of goals (currently ``relocate``). It reads belief via the Musubi tools
(claim_query) and emits a robot-agnostic plan of action types. Fully reproducible.
"""

from __future__ import annotations

from typing import Any

from agents.planner import Plan, PlanStep
from agents.tools import MusubiTools
from core.ids import mint
from ontology.generated.musubi_types import Action, ReversibilityClass


def _standoff(px: float, py: float, bx: float, by: float, distance: float) -> tuple[float, float]:
    """A point ``distance`` from (px,py) toward (bx,by) — a non-colliding approach pose."""
    dx, dy = bx - px, by - py
    norm = (dx * dx + dy * dy) ** 0.5 or 1.0
    return px + dx / norm * distance, py + dy / norm * distance


def _parse_xyz(value: str | None) -> tuple[float, float, float] | None:
    if not value:
        return None
    try:
        x, y, z = (float(v) for v in value.split(","))
        return x, y, z
    except ValueError:
        return None


class ScriptedPlanner:
    """A rule-based planner. `context` must supply `tools` (MusubiTools) and `zones` (name→(x,y))."""

    name = "scripted"

    def plan(self, goal: dict[str, Any], context: dict[str, Any]) -> Plan:
        if goal.get("type") != "relocate":
            raise ValueError(f"ScriptedPlanner does not handle goal {goal.get('type')!r}")
        tools: MusubiTools = context["tools"]
        zones: dict[str, tuple[float, float]] = context["zones"]
        entity = str(goal["entity"])
        to_zone = str(goal["to_zone"])
        body = entity.rsplit("/", 1)[-1]

        answer = tools.claim_query(entity, "position", at_time=context.get("at_time", 0.0))
        pos = _parse_xyz(answer.value)
        if pos is None:
            # bare-coupling fallback: direct sensor read (arms without the Claim layer)
            pos = context.get("direct_positions", {}).get(entity)
        if pos is None:
            raise ValueError(f"no position belief for {entity}; cannot plan relocate")
        zx, zy = zones[to_zone]
        off = goal.get("offset", (0.0, 0.0))  # distinct drop points within a zone (avoid stacking)
        zx, zy = zx + float(off[0]), zy + float(off[1])

        # Approach from a standoff on the bot's side, so the base never rams the pallet.
        bot = context.get("bot_xy", (0.0, 0.0))
        sx, sy = _standoff(pos[0], pos[1], float(bot[0]), float(bot[1]), distance=0.6)

        steps: list[PlanStep] = [
            self._step(0, "move", {"x": sx, "y": sy}, {"zone": "source"}),
            self._step(1, "transport.pick", {"pallet": body}, {}),
            self._step(2, "move", {"x": zx, "y": zy}, {"zone": to_zone}),
            self._step(3, "transport.place", {"pallet": body}, {"zone": to_zone}),
        ]
        return Plan(goal=goal, steps=steps)

    def _step(
        self, i: int, action_type: str, params: dict[str, Any], ctx: dict[str, str]
    ) -> PlanStep:
        action = Action(
            iri=mint("action", self.name, action_type, str(i)),
            actionType=action_type,
            reversibility=ReversibilityClass.reversible,
        )
        return PlanStep(action=action, action_type=action_type, params=params, context=ctx)
