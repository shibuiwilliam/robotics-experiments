"""LLM-backed planning via the provider-agnostic chat adapter (behind the VCR).

Asks the LLM for a *skeleton* — the sequence of action types to relocate an item — and grounds the
params itself via the shared :func:`agents.grounding.ground_relocate` (belief-read + standoff). This
split keeps two properties: the LLM does the *reasoning* (which steps, in what order), while the
*geometry* stays deterministic and byte-identical to the ScriptedPlanner's, so offline replay is
meaningful and the ablation ladder isolates the reasoning backend. The reasoning LLM is
registry-selected (Claude is the primary engine; Gemini is also supported) — this planner is
provider-neutral because it talks only to ``ChatAdapter``. Offline, the ChatAdapter is backed by
``FakeGeminiClient``; register a canned skeleton for the ``MUSUBI_RELOCATE_PLAN`` intent so replay is
meaningful. ``GeminiPlanner`` is the historical name; ``LLMPlanner`` is the provider-neutral alias.
"""

from __future__ import annotations

from typing import Any

from agents.grounding import ground_relocate
from agents.planner import Plan
from agents.tools import MusubiTools
from clients.chat import ChatAdapter

PLAN_INTENT = "MUSUBI_RELOCATE_PLAN"

#: JSON Schema for the plan skeleton the LLM must return (action types; params are grounded locally).
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["steps"],
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["action_type"],
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
    """LLM planner. Asks the ChatAdapter for a skeleton, then grounds params deterministically."""

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
            f"Return the ordered action types as JSON per the schema (params are grounded downstream)."
        )
        result = self._chat.generate(prompt, PLAN_SCHEMA)
        action_types = [str(raw["action_type"]) for raw in result.get("steps", [])]
        steps = ground_relocate(goal, context, action_types, planner=self.name)
        return Plan(goal=goal, steps=steps)


#: Provider-neutral name for the LLM planner (Claude or Gemini, selected via the registry).
LLMPlanner = GeminiPlanner


def make_planner(provider: str | None = None, *, offline_fake: bool = False) -> GeminiPlanner:
    """Build an LLM planner over a ChatAdapter for the given provider (default: registry)."""
    from clients.chat import make_chat_adapter

    return GeminiPlanner(make_chat_adapter(provider, offline_fake=offline_fake))
