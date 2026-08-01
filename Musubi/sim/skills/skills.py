"""Skill implementations."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from sim.world import World


@dataclass(frozen=True)
class SkillMeta:
    """Action metadata for a skill (feeds capability matching and the gate)."""

    action_type: str
    reversibility: str  # reversible | compensable | irreversible
    preconditions: tuple[str, ...] = ()
    expected_effects: tuple[str, ...] = ()
    failure_modes: tuple[str, ...] = ()
    base_failure_prob: float = 0.0


@dataclass
class SkillResult:
    success: bool
    reason: str = ""
    effect: dict[str, object] = field(default_factory=dict)


class Skill:
    """Base skill. Subclasses implement :meth:`_run`; the base handles fault injection."""

    meta: SkillMeta

    def _fault(self, world: World) -> bool:
        """Return True if this invocation fails by seeded injection."""
        if self.meta.base_failure_prob <= 0.0:
            return False
        draw = float(world.rng.generator("fault_injection").random())
        return draw < self.meta.base_failure_prob

    def execute(self, world: World) -> SkillResult:
        if self._fault(world):
            return SkillResult(False, reason=f"fault_injected:{self.meta.action_type}")
        return self._run(world)

    def _run(self, world: World) -> SkillResult:  # pragma: no cover - abstract
        raise NotImplementedError


class MoveTo(Skill):
    """Drive the lift-bot base to (x, y). Reversible."""

    meta = SkillMeta(
        action_type="move",
        reversibility="reversible",
        expected_effects=("bot_at_target",),
        failure_modes=("stuck", "timeout"),
    )

    def __init__(self, x: float, y: float, tol: float = 0.08, max_steps: int = 4000) -> None:
        self.x, self.y, self.tol, self.max_steps = x, y, tol, max_steps

    def _run(self, world: World) -> SkillResult:
        world.set_base_target(self.x, self.y)
        target = np.array([self.x, self.y])
        for _ in range(self.max_steps):
            world.step(1)
            pos = world.body_pos("lift_bot")[:2]
            if float(np.linalg.norm(pos - target)) <= self.tol:
                return SkillResult(
                    True, effect={"bot_xy": (round(float(pos[0]), 3), round(float(pos[1]), 3))}
                )
        pos = world.body_pos("lift_bot")[:2]
        return SkillResult(
            False, reason="timeout", effect={"bot_xy": (float(pos[0]), float(pos[1]))}
        )


class Pick(Skill):
    """Attach a pallet to the bot (idealized adhesion). Reversible (Place undoes it)."""

    meta = SkillMeta(
        action_type="transport.pick",
        reversibility="reversible",
        preconditions=("bot_near_pallet",),
        expected_effects=("pallet_carried",),
        failure_modes=("out_of_reach",),
    )

    def __init__(self, pallet: str, reach: float = 0.4) -> None:
        self.pallet, self.reach = pallet, reach

    def _run(self, world: World) -> SkillResult:
        if not world.is_visible(self.pallet):
            return SkillResult(False, reason="pallet_not_present")
        dist = float(
            np.linalg.norm(world.body_pos("lift_bot")[:2] - world.body_pos(self.pallet)[:2])
        )
        if dist > self.reach:
            return SkillResult(False, reason="out_of_reach", effect={"dist": round(dist, 3)})
        world.attach(self.pallet)
        world.step(1)
        return SkillResult(True, effect={"carried": self.pallet})


class Place(Skill):
    """Detach the carried pallet at the bot's current location. Reversible."""

    meta = SkillMeta(
        action_type="transport.place",
        reversibility="reversible",
        preconditions=("pallet_carried",),
        expected_effects=("pallet_placed",),
    )

    def __init__(self, pallet: str) -> None:
        self.pallet = pallet

    def _run(self, world: World) -> SkillResult:
        if not world.is_carried(self.pallet):
            return SkillResult(False, reason="not_carrying")
        world.detach(self.pallet)
        pos = world.body_pos(self.pallet)
        world.set_body_xy(self.pallet, float(pos[0]), float(pos[1]), z=0.06)
        world.step(1)
        return SkillResult(
            True, effect={"placed_at": (round(float(pos[0]), 3), round(float(pos[1]), 3))}
        )


class ScanTag(Skill):
    """Read a pallet's tag (physical anchor). Fails if the tag has been degraded."""

    meta = SkillMeta(
        action_type="perceive.scan_tag",
        reversibility="reversible",
        expected_effects=("tag_read",),
        failure_modes=("tag_unreadable",),
    )

    def __init__(self, pallet: str) -> None:
        self.pallet = pallet

    def _run(self, world: World) -> SkillResult:
        if not world.is_visible(self.pallet):
            return SkillResult(False, reason="pallet_not_present")
        if not world.tag_readable(self.pallet):
            return SkillResult(False, reason="tag_unreadable")
        return SkillResult(True, effect={"tag": self.pallet})


#: Skill classes registered by action type (used by the capability compiler, P6).
SKILL_TYPES: dict[str, type[Skill]] = {
    MoveTo.meta.action_type: MoveTo,
    Pick.meta.action_type: Pick,
    Place.meta.action_type: Place,
    ScanTag.meta.action_type: ScanTag,
}
