"""Provider-agnostic LLM engine tests — provider selection, Claude backend contract, planner."""

from __future__ import annotations

import numpy as np
import pytest

from agents import LLMPlanner, MusubiTools
from agents.gemini import PLAN_INTENT
from clients import (
    VCR,
    AnthropicBackend,
    ChatAdapter,
    FakeGeminiClient,
    GeminiBackend,
    VcrMode,
    select_chat_backend,
)
from config import load_registry
from core.claimstore import ClaimStore
from core.clock import SimClock
from core.norms import NormStore
from core.registry import CapabilityRegistry, EntityRegistry
from ontology.generated.musubi_types import Claim, Entity


def test_provider_and_model_selection(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    reg = load_registry()
    assert reg.agent_model("claude") == "claude-opus-4-8"
    assert reg.agent_model("gemini") == "gemini-3.5-flash"
    monkeypatch.setenv("MUSUBI_LLM_PROVIDER", "gemini")
    assert reg.llm_provider() == "gemini"
    monkeypatch.delenv("MUSUBI_LLM_PROVIDER", raising=False)
    assert reg.llm_provider() == "claude"  # registry default is claude (the primary engine)


def test_select_chat_backend_by_provider() -> None:
    assert isinstance(select_chat_backend("claude"), AnthropicBackend)
    assert isinstance(select_chat_backend("gemini"), GeminiBackend)


def test_anthropic_backend_refuses_er_and_embedding() -> None:
    b = AnthropicBackend()
    with pytest.raises(NotImplementedError):
        b.detect_points(np.zeros((2, 2, 3), dtype=np.uint8), "q", "claude-opus-4-8", 0)
    with pytest.raises(NotImplementedError):
        b.embed("x", "claude-opus-4-8", 768)


def test_chat_adapter_is_provider_neutral_offline(tmp_path) -> None:  # type: ignore[no-untyped-def]
    adapter = ChatAdapter(
        vcr=VCR(VcrMode.passthrough, cassette_dir=tmp_path),
        backend=FakeGeminiClient(),
        provider="claude",
    )
    assert adapter.provider == "claude" and adapter.model == "claude-opus-4-8"
    schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
    assert adapter.generate("do", schema) == {"ok": False}  # schema-valid minimal instance


def test_provider_keys_cassettes_separately(tmp_path) -> None:  # type: ignore[no-untyped-def]
    fake = FakeGeminiClient()
    fake.register_generate("X", {"answer": "A"})
    for provider in ("gemini", "claude"):
        adapter = ChatAdapter(
            vcr=VCR(VcrMode.record, cassette_dir=tmp_path), backend=fake, provider=provider
        )
        assert adapter.generate("please X") == {"answer": "A"}
    # provider is part of the VCR key -> one cassette per provider (no cross-provider replay bleed)
    assert len(list(tmp_path.glob("*.json"))) == 2


def _tools_with_position() -> MusubiTools:
    claims = ClaimStore(SimClock())
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
            source="msb:sensor",
        )
    )
    return MusubiTools(
        claims=claims, entities=entities, capabilities=CapabilityRegistry(), norms=NormStore()
    )


def test_claude_planner_offline_produces_plan(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The provider-neutral LLM planner plans via Claude offline (FakeGeminiClient canned plan)."""
    fake = FakeGeminiClient()
    fake.register_generate(
        PLAN_INTENT,
        {
            "steps": [
                {"action_type": "move", "params": {"x": -1.2, "y": 1.2}},
                {"action_type": "transport.pick", "params": {"pallet": "pallet_1"}},
            ]
        },
    )
    chat = ChatAdapter(
        vcr=VCR(VcrMode.passthrough, cassette_dir=tmp_path), backend=fake, provider="claude"
    )
    planner = LLMPlanner(chat)
    plan = planner.plan(
        {"type": "relocate", "entity": "msb:entity/pallet_1", "to_zone": "shipping"},
        {"tools": _tools_with_position(), "zones": {"shipping": (1.2, -1.2)}},
    )
    assert [s.action_type for s in plan.steps] == ["move", "transport.pick"]
    assert chat.provider == "claude"
