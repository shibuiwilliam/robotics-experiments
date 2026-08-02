"""Shared grounding for relocate plans — turn an action-type *skeleton* into a grounded Plan.

Both planners author the same relocate skeleton (move-standoff → pick → move-zone → place); only who
*authored* it differs (``ScriptedPlanner`` emits it directly; ``LLMPlanner`` asks the LLM for it).
Grounding — resolving believed position, drop zone, and a non-colliding standoff into concrete
params — is identical and deterministic, so it lives here. This keeps A0/A1 (scripted) and A2–A4
(LLM) plans byte-for-byte comparable: the ablation varies the *reasoning backend*, not the geometry.
"""

from __future__ import annotations

from typing import Any

from agents.planner import PlanStep
from core.ids import mint
from ontology.generated.musubi_types import Action, ReversibilityClass

#: The canonical relocate skeleton (action types only). Both planners ground this same sequence.
RELOCATE_SKELETON: list[str] = ["move", "transport.pick", "move", "transport.place"]


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


def _step(
    planner: str, i: int, action_type: str, params: dict[str, Any], ctx: dict[str, str]
) -> PlanStep:
    action = Action(
        iri=mint("action", planner, action_type, str(i)),
        actionType=action_type,
        reversibility=ReversibilityClass.reversible,
    )
    return PlanStep(action=action, action_type=action_type, params=params, context=ctx)


def ground_relocate(
    goal: dict[str, Any], context: dict[str, Any], action_types: list[str], *, planner: str
) -> list[PlanStep]:
    """Ground a relocate ``action_types`` skeleton into concrete :class:`PlanStep`\\ s.

    ``context`` must supply ``tools`` (MusubiTools) and ``zones`` (name→(x,y)); optionally ``bot_xy``,
    ``direct_positions`` (bare-coupling fallback), and ``at_time``. The two ``move`` actions are
    grounded positionally: the first approaches the source pallet from a standoff, the second targets
    the destination zone (with per-item offset). Reads belief via ``claim_query`` — never sim truth.
    """
    tools = context["tools"]
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
    bot = context.get("bot_xy", (0.0, 0.0))
    sx, sy = _standoff(pos[0], pos[1], float(bot[0]), float(bot[1]), distance=0.6)

    steps: list[PlanStep] = []
    moves = 0
    for i, action_type in enumerate(action_types):
        params: dict[str, Any]
        ctx: dict[str, str]
        if action_type == "move":
            if moves == 0:  # approach the source pallet from a standoff (never ram it)
                params, ctx = {"x": sx, "y": sy}, {"zone": "source"}
            else:  # carry to the destination zone
                params, ctx = {"x": zx, "y": zy}, {"zone": to_zone}
            moves += 1
        elif action_type == "transport.pick":
            params, ctx = {"pallet": body}, {}
        elif action_type == "transport.place":
            params, ctx = {"pallet": body}, {"zone": to_zone}
        else:
            params, ctx = {}, {}
        steps.append(_step(planner, i, action_type, params, ctx))
    return steps
