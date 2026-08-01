"""Scenario assembly — wire world + core + perception + agent from a Scenario (or the E0 default).

One place builds the whole stack so every scenario, the E0 smoke, and the CLI share it. The arm
toggles which machinery is active; the dial selects perception; the Invisible Hand plants the
divergence the oracle later scores.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents.capability_compiler.compiler import tools_for
from agents.gemini import GeminiPlanner
from agents.planner import Planner
from agents.scripted import ScriptedPlanner
from agents.tools import MusubiTools
from bench.runner.arm import Arm
from bench.runner.episode import Episode, EpisodeResult
from bench.scenarios.loader import Scenario
from clients.chat import ChatAdapter
from core.bus import EventBus
from core.claimstore import ClaimStore
from core.mediator import Mediator
from core.norms import NormStore
from core.registry import CapabilityRegistry, EntityRegistry
from ontology.generated.musubi_types import (
    Capability,
    Entity,
    Norm,
    NormModality,
    NormStrength,
)
from perception.oracle import OraclePerception
from sim.invisible_hand import InvisibleHand
from sim.world import ZONES, World

_LIFT_BOT = "msb:robot/lift_bot"


def _make_norm(spec: dict[str, Any]) -> Norm:
    return Norm(
        iri=str(spec["iri"]),
        modality=NormModality(spec.get("modality", "prohibition")),
        strength=NormStrength(spec.get("strength", "hard")),
        scope=list(spec.get("scope", [])),
        normSource=spec.get("normSource"),
        regime=spec.get("regime"),
        priority=int(spec.get("priority", 0)),
    )


@dataclass
class Stack:
    """The assembled components of a scenario run (shared by Episode and flagship drivers)."""

    world: World
    arm: Arm
    tools: MusubiTools
    bus: EventBus
    skill_registry: dict[str, Any]
    zones: dict[str, tuple[float, float]]
    perception: OraclePerception
    perturbations: list[dict[str, Any]]


def build_stack(scenario: Scenario, arm_name: str, seed: int) -> Stack:
    """Assemble world + core + perception + skills + planted divergence for a scenario run."""
    world = World(seed=seed)
    arm = Arm.from_name(arm_name)

    hand = InvisibleHand(world)  # plant divergence before anything reads the world
    perturbations = [
        {"op": p.op, "targets": list(p.targets), "detail": p.detail}
        for p in hand.apply_schedule(scenario.invisible_hand)
    ]

    claims = ClaimStore(world.clock)
    entities = EntityRegistry()
    capabilities = CapabilityRegistry()
    norms = NormStore()
    for spec in scenario.norms:
        norms.add(_make_norm(spec))
    for body in world.pallet_bodies():
        entities.register(Entity(iri=world.entity_iri(body), label=body))
    capabilities.advertise(
        Capability(
            iri="msb:cap/lift_bot", actor=_LIFT_BOT, actionTypes=["move", "transport", "perceive"]
        )
    )
    skill_registry = tools_for(capabilities.capabilities(), _LIFT_BOT)
    tools = MusubiTools(
        claims=claims,
        entities=entities,
        capabilities=capabilities,
        norms=norms,
        mediator=Mediator(claims, entities),
    )
    zones = {name: (cx, cy) for name, (cx, cy, _half) in ZONES.items()}
    return Stack(
        world=world,
        arm=arm,
        tools=tools,
        bus=EventBus(),
        skill_registry=skill_registry,
        zones=zones,
        perception=OraclePerception(world),
        perturbations=perturbations,
    )


def build_from_scenario(
    scenario: Scenario,
    arm_name: str,
    seed: int,
    planner: Planner | None = None,
    *,
    chat: ChatAdapter | None = None,
) -> tuple[Episode, dict[str, Any], World, list[dict[str, Any]]]:
    """Assemble a relocate-style scenario run. Returns (episode, goal, world, perturbations)."""
    stack = build_stack(scenario, arm_name, seed)
    if planner is None:
        planner = GeminiPlanner(chat) if chat is not None else ScriptedPlanner()
    episode = Episode(
        world=stack.world,
        tools=stack.tools,
        planner=planner,
        bus=stack.bus,
        skill_registry=stack.skill_registry,
        zones=stack.zones,
        arm=stack.arm,
        detect=stack.perception.detect,
    )
    return episode, dict(scenario.goal), stack.world, stack.perturbations


# --------------------------------------------------------------------------- E0 convenience
def _e0_scenario() -> Scenario:
    return Scenario(
        name="e0_smoke",
        goal={"type": "relocate", "entity": "msb:entity/pallet_1", "to_zone": "shipping"},
        arms=["A0", "A1", "A2", "A3", "A4"],
        seeds=[0],
    )


def build_e0(
    seed: int = 0,
    arm_name: str = "A4",
    planner: Planner | None = None,
    *,
    chat: ChatAdapter | None = None,
) -> tuple[Episode, dict[str, Any], World]:
    episode, goal, world, _ = build_from_scenario(
        _e0_scenario(), arm_name, seed, planner, chat=chat
    )
    return episode, goal, world


def run_e0(seed: int = 0, arm_name: str = "A4", planner: Planner | None = None) -> EpisodeResult:
    episode, goal, _world = build_e0(seed, arm_name, planner)
    return episode.run(goal)
