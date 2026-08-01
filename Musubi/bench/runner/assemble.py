"""Scenario assembly — wire world + core + perception + agent from a Scenario (or the E0 default).

One place builds the whole stack so every scenario, the E0 smoke, and the CLI share it. The arm
toggles which machinery is active; the dial selects perception; the Invisible Hand plants the
divergence the oracle later scores.
"""

from __future__ import annotations

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


def build_from_scenario(
    scenario: Scenario,
    arm_name: str,
    seed: int,
    planner: Planner | None = None,
    *,
    chat: ChatAdapter | None = None,
) -> tuple[Episode, dict[str, Any], World, list[dict[str, Any]]]:
    """Assemble a scenario run. Returns (episode, goal, world, planted_perturbations)."""
    world = World(seed=seed)
    arm = Arm.from_name(arm_name)

    # Invisible Hand: plant divergence before the episode reads the world.
    hand = InvisibleHand(world)
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
    bus = EventBus()
    zones = {name: (cx, cy) for name, (cx, cy, _half) in ZONES.items()}

    if planner is None:
        planner = GeminiPlanner(chat) if chat is not None else ScriptedPlanner()

    detect = OraclePerception(world).detect  # oracle dial (offline)

    episode = Episode(
        world=world,
        tools=tools,
        planner=planner,
        bus=bus,
        skill_registry=skill_registry,
        zones=zones,
        arm=arm,
        detect=detect,
    )
    return episode, dict(scenario.goal), world, perturbations


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
