"""P6 agent tests — capability compiler, ScriptedPlanner, Musubi tools, GeminiPlanner (offline)."""

from __future__ import annotations

from agents import GeminiPlanner, ScriptedPlanner, compile_capabilities
from agents.gemini import PLAN_INTENT
from agents.tools import MusubiTools
from clients import VCR, ChatAdapter, FakeGeminiClient, VcrMode
from core.claimstore import ClaimStore
from core.clock import SimClock
from core.norms import NormStore
from core.registry import CapabilityRegistry, EntityRegistry
from ontology.generated.musubi_types import Capability, Claim, Entity, Norm


def _tools_with_position() -> MusubiTools:
    clk = SimClock()
    claims = ClaimStore(clk)
    entities = EntityRegistry()
    entities.register(Entity(iri="msb:entity/pallet_1", label="pallet_1"))
    claims.add(
        Claim(
            iri="msb:claim/p1",
            claimKind="position",
            subject="msb:entity/pallet_1",
            predicate="position",
            objectValue="-1.2,1.2,0.06",
            confidence=0.95,
            realm="real",
            method="direct_measurement",
            source="msb:sensor/oracle_cam",
        )
    )
    return MusubiTools(
        claims=claims, entities=entities, capabilities=CapabilityRegistry(), norms=NormStore()
    )


def test_capability_compiler_binds_skills_zero_agent_code() -> None:
    cap = Capability(
        iri="msb:cap/x", actor="msb:robot/x", actionTypes=["move", "transport", "perceive"]
    )
    tools = compile_capabilities([cap])
    action_types = {t.action_type for t in tools}
    assert {"move", "transport.pick", "transport.place", "perceive.scan_tag"} <= action_types


def test_scripted_planner_produces_relocate_plan() -> None:
    tools = _tools_with_position()
    goal = {"type": "relocate", "entity": "msb:entity/pallet_1", "to_zone": "shipping"}
    plan = ScriptedPlanner().plan(
        goal, {"tools": tools, "zones": {"shipping": (1.2, -1.2)}, "bot_xy": (0.0, -1.2)}
    )
    assert [s.action_type for s in plan.steps] == [
        "move",
        "transport.pick",
        "move",
        "transport.place",
    ]
    assert all(str(s.action.reversibility) == "reversible" for s in plan.steps)


def test_musubi_tools_entity_resolve_and_claim_query() -> None:
    tools = _tools_with_position()
    assert tools.entity_resolve("pallet_1") == "msb:entity/pallet_1"
    assert tools.entity_resolve("ghost") is None
    ans = tools.claim_query("msb:entity/pallet_1", "position")
    assert ans.value == "-1.2,1.2,0.06" and ans.confidence > 0.9


def test_musubi_tools_norm_check() -> None:
    norms = NormStore()
    norms.add(
        Norm(
            iri="msb:norm/no-dispose",
            modality="prohibition",
            strength="hard",
            scope=["actionType:dispose"],
        )
    )
    tools = MusubiTools(
        claims=ClaimStore(SimClock()),
        entities=EntityRegistry(),
        capabilities=CapabilityRegistry(),
        norms=norms,
    )
    assert tools.norm_check("dispose", {}) == ["msb:norm/no-dispose"]
    assert tools.norm_check("move", {}) == []


def test_gemini_planner_offline_with_fake(tmp_path) -> None:  # type: ignore[no-untyped-def]
    fake = FakeGeminiClient()
    fake.register_generate(
        PLAN_INTENT,
        {
            "steps": [
                {"action_type": "move", "params": {"x": -1.2, "y": 1.2}, "context": {}},
                {"action_type": "transport.pick", "params": {"pallet": "pallet_1"}},
            ]
        },
    )
    chat = ChatAdapter(vcr=VCR(VcrMode.passthrough, cassette_dir=tmp_path), backend=fake)
    planner = GeminiPlanner(chat)
    goal = {"type": "relocate", "entity": "msb:entity/pallet_1", "to_zone": "shipping"}
    plan = planner.plan(goal, {"tools": _tools_with_position(), "zones": {"shipping": (1.2, -1.2)}})
    assert [s.action_type for s in plan.steps] == ["move", "transport.pick"]
