"""ScriptedPlanner — deterministic planning with no LLM (offline bootstrap + lower ablation arms).

Understands a small set of goals (currently ``relocate``). It emits the canonical relocate skeleton
and grounds it via the shared :func:`agents.grounding.ground_relocate` (belief-read + standoff), so
its plan is byte-identical to the LLM planner's for the same world. Fully reproducible.
"""

from __future__ import annotations

from typing import Any

from agents.grounding import RELOCATE_SKELETON, ground_relocate
from agents.planner import Plan


class ScriptedPlanner:
    """A rule-based planner. `context` must supply `tools` (MusubiTools) and `zones` (name→(x,y))."""

    name = "scripted"

    def plan(self, goal: dict[str, Any], context: dict[str, Any]) -> Plan:
        if goal.get("type") != "relocate":
            raise ValueError(f"ScriptedPlanner does not handle goal {goal.get('type')!r}")
        steps = ground_relocate(goal, context, RELOCATE_SKELETON, planner=self.name)
        return Plan(goal=goal, steps=steps)
