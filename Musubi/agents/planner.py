"""Planner interface + plan data model.

Two interchangeable backends implement :class:`Planner`: the deterministic ``ScriptedPlanner``
(no LLM — offline bootstrap, CI smoke, lower ablation arms) and ``GeminiPlanner`` (ADK, behind the
VCR). This is what lets the platform be proven working with zero API dependency, then have real
intelligence layered in (build-prompt §2).

A plan step names an *action type* + params (never a concrete robot skill) — the capability
compiler binds action types to a robot's skills, so the planner is robot-agnostic (E4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ontology.generated.musubi_types import Action


@dataclass
class PlanStep:
    """One plan step: an ontology Action (for gating/capability) + how to execute it abstractly."""

    action: Action
    action_type: str
    params: dict[str, Any] = field(default_factory=dict)
    context: dict[str, str] = field(default_factory=dict)  # zone/lot/... for norm checks
    approved: bool = False  # explicit human approval (irreversible gate)


@dataclass
class Plan:
    """An ordered plan for a goal."""

    goal: dict[str, Any]
    steps: list[PlanStep] = field(default_factory=list)

    def actions(self) -> list[Action]:
        return [s.action for s in self.steps]

    def approvals(self) -> frozenset[str]:
        return frozenset(str(s.action.iri) for s in self.steps if s.approved)

    def action_context(self) -> dict[str, dict[str, str]]:
        return {str(s.action.iri): s.context for s in self.steps}


class Planner(Protocol):
    """Produces a Plan for a goal, given a planning context (tools, world facts)."""

    name: str  # planner identity (e.g. "scripted", "gemini") — used in action IRIs + observability

    def plan(self, goal: dict[str, Any], context: dict[str, Any]) -> Plan: ...
