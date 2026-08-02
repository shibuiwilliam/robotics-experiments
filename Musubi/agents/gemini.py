"""LLM-backed planning via the provider-agnostic chat adapter (behind the VCR).

Requests a structured plan (JSON) and converts it to a robot-agnostic Plan. The reasoning LLM is
registry-selected (Claude is the primary engine; Gemini is also supported) — this planner is
provider-neutral because it talks only to ``ChatAdapter``. Offline, the ChatAdapter is backed by
``FakeGeminiClient``; register a canned plan for the ``MUSUBI_RELOCATE_PLAN`` intent so replay is
meaningful. ``GeminiPlanner`` is the historical name; ``LLMPlanner`` is the provider-neutral alias.
"""

from __future__ import annotations

from typing import Any

from agents.planner import Plan, PlanStep
from agents.tools import MusubiTools
from clients.chat import ChatAdapter
from core.ids import mint
from ontology.generated.musubi_types import Action, ReversibilityClass

PLAN_INTENT = "MUSUBI_RELOCATE_PLAN"

#: JSON Schema for the structured plan the LLM must return.
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["steps"],
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["action_type", "params"],
                "properties": {
                    "action_type": {"type": "string"},
                    "params": {"type": "object"},
                    "context": {"type": "object"},
                    "reversibility": {
                        "type": "string",
                        "enum": ["reversible", "compensable", "irreversible"],
                    },
                    "approved": {"type": "boolean"},
                },
            },
        }
    },
}


class GeminiPlanner:
    """LLM planner. Uses the ChatAdapter for structured generation; parses into a Plan."""

    name = "gemini"

    def __init__(self, chat: ChatAdapter) -> None:
        self._chat = chat

    def plan(self, goal: dict[str, Any], context: dict[str, Any]) -> Plan:
        tools: MusubiTools = context["tools"]
        entity = str(goal["entity"])
        answer = tools.claim_query(entity, "position", at_time=context.get("at_time", 0.0))
        prompt = (
            f"{PLAN_INTENT}: produce a plan to relocate {entity} to zone {goal.get('to_zone')}. "
            f"Believed position: {answer.value} (confidence {answer.confidence:.2f}). "
            f"Return steps as JSON per the schema."
        )
        result = self._chat.generate(prompt, PLAN_SCHEMA)
        steps: list[PlanStep] = []
        for i, raw in enumerate(result.get("steps", [])):
            rev = ReversibilityClass(raw.get("reversibility", "reversible"))
            action = Action(
                iri=mint("action", self.name, str(raw["action_type"]), str(i)),
                actionType=str(raw["action_type"]),
                reversibility=rev,
            )
            steps.append(
                PlanStep(
                    action=action,
                    action_type=str(raw["action_type"]),
                    params=dict(raw.get("params", {})),
                    context={str(k): str(v) for k, v in raw.get("context", {}).items()},
                    approved=bool(raw.get("approved", False)),
                )
            )
        return Plan(goal=goal, steps=steps)


#: Provider-neutral name for the LLM planner (Claude or Gemini, selected via the registry).
LLMPlanner = GeminiPlanner


def make_planner(provider: str | None = None, *, offline_fake: bool = False) -> GeminiPlanner:
    """Build an LLM planner over a ChatAdapter for the given provider (default: registry)."""
    from clients.chat import make_chat_adapter

    return GeminiPlanner(make_chat_adapter(provider, offline_fake=offline_fake))
