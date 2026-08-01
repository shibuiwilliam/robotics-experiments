"""Half-idealized robot skills with action metadata + seeded fault injection.

Each skill is an Action realization (Skill) carrying preconditions / expected effects / failure
modes / reversibility (unified action model, PROJECT.md §6.3). Failure is injected by probability
from the seeded ``fault_injection`` sub-stream (PROJECT.md §3.2), never by physics tuning.
"""

from __future__ import annotations

from sim.skills.skills import (
    SKILL_TYPES,
    MoveTo,
    Pick,
    Place,
    ScanTag,
    Skill,
    SkillMeta,
    SkillResult,
)

__all__ = [
    "Skill",
    "SkillMeta",
    "SkillResult",
    "MoveTo",
    "Pick",
    "Place",
    "ScanTag",
    "SKILL_TYPES",
]
