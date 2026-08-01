"""E0 assembly — wire world + core + perception + agent for the smoke scenario.

E0 is the vertical-slice smoke: a lift-bot relocates a pallet from receiving to shipping, fully
offline (oracle dial + ScriptedPlanner). This factory builds the whole stack so the E0 test and
the CLI share one assembly. The higher ablation arms toggle which machinery is active.
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
from clients.chat import ChatAdapter
from core.bus import EventBus
from core.claimstore import ClaimStore
from core.mediator import Mediator
from core.norms import NormStore
from core.registry import CapabilityRegistry, EntityRegistry
from ontology.generated.musubi_types import Capability, Entity
from perception.oracle import OraclePerception
from sim.world import ZONES, World

_LIFT_BOT = "msb:robot/lift_bot"


def build_e0(
    seed: int = 0,
    arm_name: str = "A4",
    planner: Planner | None = None,
    *,
    chat: ChatAdapter | None = None,
) -> tuple[Episode, dict[str, Any], World]:
    """Assemble the E0 stack. Returns (episode, goal, world)."""
    world = World(seed=seed)
    arm = Arm.from_name(arm_name)

    claims = ClaimStore(world.clock)
    entities = EntityRegistry()
    capabilities = CapabilityRegistry()
    norms = NormStore()

    # register pallet entities (identity) so entity_resolve / registry work
    for body in world.pallet_bodies():
        entities.register(Entity(iri=world.entity_iri(body), label=body))

    # the lift-bot advertises capabilities; the compiler turns them into skills (zero agent code).
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

    oracle = OraclePerception(world)
    episode = Episode(
        world=world,
        tools=tools,
        planner=planner,
        bus=bus,
        skill_registry=skill_registry,
        zones=zones,
        arm=arm,
        detect=oracle.detect,
    )
    goal = {"type": "relocate", "entity": world.entity_iri("pallet_1"), "to_zone": "shipping"}
    return episode, goal, world


def run_e0(seed: int = 0, arm_name: str = "A4", planner: Planner | None = None) -> EpisodeResult:
    """Build and run the E0 smoke episode."""
    episode, goal, _world = build_e0(seed, arm_name, planner)
    return episode.run(goal)
