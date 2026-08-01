"""Capability→tool compiler implementation."""

from __future__ import annotations

from dataclasses import dataclass

from core.registry.capability import subsumes
from ontology.generated.musubi_types import Capability
from sim.skills import SKILL_TYPES, Skill


@dataclass(frozen=True)
class CompiledTool:
    """A skill made available to an actor because a capability subsumes its action type."""

    actor: str
    action_type: str
    skill_type: type[Skill]

    def build(self, **kwargs: object) -> Skill:
        return self.skill_type(**kwargs)


def compile_capabilities(capabilities: list[Capability]) -> list[CompiledTool]:
    """Compile advertised capabilities into concrete tools (skills) — no agent code required.

    For every registered skill type, if any of an actor's advertised action types subsumes the
    skill's action type, the actor gets that skill as a tool.
    """
    tools: list[CompiledTool] = []
    for cap in capabilities:
        actor = str(cap.actor)
        for skill_type_name, skill_cls in SKILL_TYPES.items():
            if any(subsumes(str(at), skill_type_name) for at in cap.actionTypes):
                tools.append(CompiledTool(actor, skill_type_name, skill_cls))
    return tools


def tools_for(capabilities: list[Capability], actor: str) -> dict[str, type[Skill]]:
    """Map action_type -> Skill class for one actor (its usable toolbox)."""
    return {
        t.action_type: t.skill_type for t in compile_capabilities(capabilities) if t.actor == actor
    }
