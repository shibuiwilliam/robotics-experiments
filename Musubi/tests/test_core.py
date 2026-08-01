"""P2 core tests — bus (toxic), capability subsumption, mediation ordering, gate, explain."""

from __future__ import annotations

from core.bus import EventBus, ToxicConfig, make_envelope
from core.claimstore import ClaimStore
from core.clock import SimClock
from core.explain import accountability_chain, trace_completeness, unresolved_references
from core.mediator import Mediator
from core.norms import Gate, NormStore
from core.registry import CapabilityRegistry, EntityRegistry, subsumes
from core.rng import RngRegistry
from ontology.generated.musubi_types import (
    Action,
    Authority,
    Capability,
    Claim,
    Norm,
    QuantityValue,
    Requirement,
)


# ------------------------------------------------------------------ bus
def test_bus_delivers_and_traces() -> None:
    bus = EventBus()
    got: list[str] = []
    bus.subscribe("Detected", lambda e: got.append(e.id))
    bus.publish(make_envelope("Detected", {"x": 1}, id="msb:ev/1", trace="msb:case/1"))
    bus.publish(make_envelope("Detected", {"x": 2}, id="msb:ev/2", trace="msb:case/2"))
    assert got == ["msb:ev/1", "msb:ev/2"]
    assert [e.id for e in bus.trace("msb:case/1")] == ["msb:ev/1"]


def test_bus_toxic_partition_drops() -> None:
    bus = EventBus(toxic=ToxicConfig(partitioned={"msb:robot/2"}))
    got: list[str] = []
    bus.subscribe("*", lambda e: got.append(e.id))
    assert bus.publish(make_envelope("E", {}, id="msb:ev/a", source="msb:robot/1")) is True
    assert bus.publish(make_envelope("E", {}, id="msb:ev/b", source="msb:robot/2")) is False
    assert got == ["msb:ev/a"]


def test_bus_toxic_drop_is_deterministic() -> None:
    rng = RngRegistry(0).generator("bus")
    bus = EventBus(rng=rng, toxic=ToxicConfig(drop_prob=0.5))
    results = [bus.publish(make_envelope("E", {}, id=f"msb:ev/{i}")) for i in range(20)]
    rng2 = RngRegistry(0).generator("bus")
    bus2 = EventBus(rng=rng2, toxic=ToxicConfig(drop_prob=0.5))
    results2 = [bus2.publish(make_envelope("E", {}, id=f"msb:ev/{i}")) for i in range(20)]
    assert results == results2  # same seed -> same drops


# ------------------------------------------------------------------ capability
def test_subsumption_matching() -> None:
    assert subsumes("transport", "transport.heavy")
    assert subsumes("transport", "transport")
    assert not subsumes("transport.heavy", "transport")
    assert not subsumes("inspect", "transport")


def test_capability_registry_matches_by_subsumption_and_qos() -> None:
    reg = CapabilityRegistry()
    reg.advertise(
        Capability(
            iri="msb:cap/liftbot",
            actor="msb:robot/lift",
            actionTypes=["transport"],
            qos=[QuantityValue(magnitude=100.0, unit="qudt:KG")],
        )
    )
    ok = Requirement(
        iri="msb:req/1",
        actionType="transport.heavy",
        qosConstraints=[QuantityValue(magnitude=80.0, unit="qudt:KG")],
    )
    too_heavy = Requirement(
        iri="msb:req/2",
        actionType="transport.heavy",
        qosConstraints=[QuantityValue(magnitude=120.0, unit="qudt:KG")],
    )
    assert [c.iri for c in reg.match(ok)] == ["msb:cap/liftbot"]
    assert reg.match(too_heavy) == []


# ------------------------------------------------------------------ mediation
def _claim(iri: str, conf: float, method: str, kind: str = "position", **kw: object) -> Claim:
    return Claim(
        iri=iri,
        claimKind=kind,
        subject="msb:e/pallet",
        predicate="position",
        confidence=conf,
        realm="real",
        method=method,
        **kw,
    )


def test_mediation_prefers_authority_and_method() -> None:
    clk = SimClock()
    store = ClaimStore(clk)
    # ledger says A (older, method ledger); fresh sensor says B (direct measurement).
    store.add(_claim("msb:c/ledger", 0.9, "ledger_of_record", source="msb:wms"))
    store.add(_claim("msb:c/sensor", 0.9, "direct_measurement", source="msb:cam"))
    med = Mediator(store)
    outcome = med.resolve("msb:e/pallet", "position", at_time=0.0)
    assert outcome is not None
    # direct_measurement (rank 5) beats ledger_of_record (rank 3) at equal confidence.
    assert outcome.winner.iri == "msb:c/sensor"


def test_mediation_commit_supersedes_losers_appendonly() -> None:
    clk = SimClock()
    store = ClaimStore(clk)
    store.add(_claim("msb:c/ledger", 0.9, "ledger_of_record", source="msb:wms"))
    store.add(_claim("msb:c/sensor", 0.9, "direct_measurement", source="msb:cam"))
    med = Mediator(store)
    outcome = med.resolve("msb:e/pallet", "position", 0.0)
    assert outcome is not None
    med.commit(outcome, at_time=0.0)
    active = store.active(subject="msb:e/pallet", predicate="position")
    assert [c.iri for c in active] == ["msb:c/sensor"]
    # loser never deleted
    assert store.get("msb:c/ledger") is not None


def test_authority_reputation_shifts_mediation() -> None:
    clk = SimClock()
    store = ClaimStore(clk)
    entities = EntityRegistry()
    # cam is a low-reputation authority (S9 drift); wms trusted.
    entities.declare_authority(
        Authority(iri="msb:cam", aspectKind="physical", effectiveConfidence=0.1)
    )
    store.add(_claim("msb:c/ledger", 0.9, "ledger_of_record", source="msb:wms"))
    store.add(_claim("msb:c/sensor", 0.9, "direct_measurement", source="msb:cam"))
    med = Mediator(store, entities)
    outcome = med.resolve("msb:e/pallet", "position", 0.0)
    assert outcome is not None
    # cam's reputation (0.1) sinks its score below the trusted ledger.
    assert outcome.winner.iri == "msb:c/ledger"


# ------------------------------------------------------------------ gate
def test_gate_blocks_unapproved_irreversible() -> None:
    gate = Gate(NormStore())
    dispose = Action(iri="msb:act/dispose", actionType="dispose", reversibility="irreversible")
    res = gate.check_plan([dispose])
    assert not res.ok
    assert res.unapproved_irreversible() == 1
    # with approval it passes
    ok = gate.check_plan([dispose], approvals=frozenset({"msb:act/dispose"}))
    assert ok.ok


def test_gate_enforces_prohibition_norm() -> None:
    norms = NormStore()
    norms.add(
        Norm(
            iri="msb:norm/no-contact-L789",
            modality="prohibition",
            strength="hard",
            scope=["actionType:contact", "lot:L789"],
        )
    )
    gate = Gate(norms)
    contact = Action(iri="msb:act/contact", actionType="contact", reversibility="reversible")
    ctx = {"msb:act/contact": {"lot": "L789"}}
    res = gate.check_plan([contact], action_context=ctx)
    assert not res.ok
    assert any(v.kind == "prohibited_action" for v in res.violations)
    # a different lot is fine
    res_ok = gate.check_plan([contact], action_context={"msb:act/contact": {"lot": "L000"}})
    assert res_ok.ok


# ------------------------------------------------------------------ explain
def test_accountability_chain_walks_justifications() -> None:
    clk = SimClock()
    store = ClaimStore(clk)
    store.add(_claim("msb:c/ground", 1.0, "ledger_of_record", source="msb:wms"))
    store.add(_claim("msb:c/derived", 0.9, "inference", justifiedBy=["msb:c/ground"]))
    chain = accountability_chain(store, "msb:c/derived")
    assert [c.iri for c in chain] == ["msb:c/derived", "msb:c/ground"]
    assert trace_completeness(store, ["msb:c/derived"]) == 1.0
    assert unresolved_references(store, ["msb:c/derived", "msb:c/missing"]) == {"msb:c/missing"}
