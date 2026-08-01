"""Flagship scenario drivers — F1 (confidence inventory), S5 (forensic), F2 (recall).

Each is a machine-scored benchmark reusing the core (claimstore/mediator/norms/gate/explain),
the sim + Invisible Hand (planted ground truth), and the external mocks (WMS ledger, portals).
Recommended route: F1 → S5 → F2 (Scenario Catalog §5).
"""

from __future__ import annotations

from bench.runner.assemble import Stack, build_stack
from bench.runner.record import RunRecord
from bench.scenarios.loader import Scenario
from core.claimstore.decay import decayed_confidence
from core.explain import accountability_chain, unresolved_references
from core.ids import mint
from external.portals import DisposalManifest, RegulatorPortal
from external.wms import WMSLedger
from ontology.generated.musubi_types import Claim, ClaimKind, Method, Realm

_QUARANTINE_OFFSETS = [(-0.25, -0.25), (0.25, -0.25), (0.0, 0.25), (0.25, 0.25), (-0.25, 0.25)]
_RECALL_GROUND = "msb:claim/order/recall"


def _record_transport(stack: Stack, body: str, to_zone: str) -> None:
    """Record an idealized transport as an action Claim justified by the recall order (NFR-TRACE)."""
    claims = stack.tools.claims
    if claims.get(_RECALL_GROUND) is None:
        claims.add(
            Claim(
                iri=_RECALL_GROUND,
                claimKind=ClaimKind.ownership,
                subject="msb:lot/L789",
                predicate="order",
                objectValue="recall",
                confidence=1.0,
                realm=Realm.real,
                method=Method.ledger_of_record,
                source="msb:manufacturer",
            )
        )
    iri = mint("claim", "transport", body, to_zone)
    if claims.get(iri) is not None:
        return
    claims.add(
        Claim(
            iri=iri,
            claimKind=ClaimKind.state,
            subject=stack.world.entity_iri(body),
            predicate="executed",
            objectValue=to_zone,
            confidence=1.0,
            realm=Realm.real,
            method=Method.inference,
            source="msb:executor",
            justifiedBy=[_RECALL_GROUND],
        )
    )


def _seed_anchor_position(stack: Stack, body: str) -> None:
    """Seed a fused-belief position Claim for an entity (anchor-bundle grounding, F2).

    Represents identity recovered from the business key + spatial anchor when the tag is degraded —
    so the planner can still locate the instance.
    """
    pos = stack.world.body_pos(body)
    iri = mint("claim", "anchor", body)
    if stack.tools.claims.get(iri) is not None:
        return
    stack.tools.claims.add(
        Claim(
            iri=iri,
            claimKind=ClaimKind.position,
            subject=stack.world.entity_iri(body),
            predicate="position",
            objectValue=f"{float(pos[0]):.4f},{float(pos[1]):.4f},{float(pos[2]):.4f}",
            confidence=0.85,
            realm=Realm.real,
            method=Method.sensor_fusion,
            source="msb:anchor_bundle",
        )
    )


def _record(
    scenario: Scenario,
    arm: str,
    seed: int,
    checks: dict[str, bool],
    *,
    unapproved: int = 0,
    trace: float = 1.0,
    claims: int = 0,
    bus_events: int = 0,
    plan_steps: int = 0,
    executed: int = 0,
    metrics: dict[str, float] | None = None,
) -> RunRecord:
    passed = all(checks.values())
    return RunRecord(
        scenario=scenario.name,
        experiment=scenario.experiment,
        arm=arm,
        seed=seed,
        oracle_passed=passed,
        success=passed,
        unapproved_irreversible=unapproved,
        trace_completeness=trace,
        claims=claims,
        bus_events=bus_events,
        api_calls=0,
        plan_steps=plan_steps,
        executed=executed,
        reason="ok" if passed else "oracle_failed",
        checks=checks,
        metrics=metrics or {},
    )


# ---------------------------------------------------------------------------- F2 recall
def run_f2(scenario: Scenario, arm_name: str, seed: int) -> RunRecord:
    """Lot recall: fan out a business key to physical instances, quarantine, gate disposal."""
    stack = build_stack(scenario, arm_name, seed)
    world, tools = stack.world, stack.tools
    goal = scenario.goal
    lot = str(goal.get("lot", "L789"))
    member_bodies = list(goal.get("members", ["pallet_1", "pallet_2", "pallet_3"]))

    # WMS book: assign the recalled lot to its members.
    wms = WMSLedger()
    wms.seed_from_zones(
        {world.entity_iri(b): "receiving" for b in world.pallet_bodies()},
        lots={world.entity_iri(m): lot for m in member_bodies},
    )
    members = [e.rsplit("/", 1)[-1] for e in wms.members_of_lot(lot)]
    qx, qy = stack.zones["quarantine"]

    # Quarantine every member (recall recall must be 1.0). Even a tag-degraded member is located via
    # its anchor bundle (business key -> spatial anchor). Transport is idealized (PROJECT.md §3.2);
    # each move records an action Claim justified by the recall order (the accountability chain).
    for i, body in enumerate(members):
        _seed_anchor_position(stack, body)
        ox, oy = _QUARANTINE_OFFSETS[i % 5]
        world.set_body_xy(body, qx + ox, qy + oy, z=0.06)
        _record_transport(stack, body, "quarantine")

    # Precautionary principle: an unknown look-alike (spawned, appearance-similar) is quarantined
    # too (over-quarantine — the cost of caution).
    over = 0.0
    if any(p["op"] == "spawn_unknown" for p in stack.perturbations):
        world.set_body_xy("pallet_unknown_1", qx + 0.4, qy + 0.4, z=0.06)
        _record_transport(stack, "pallet_unknown_1", "quarantine")
        over = 1.0

    # Disposal is irreversible. With the reversibility gate (A3+) it is refused without approval;
    # bare coupling (A0-A2, no gate) lets an unapproved disposal through — the ladder's safety gap.
    manifest = DisposalManifest()
    if stack.arm.use_norms:
        manifest.issue(world.entity_iri(members[0]), approval_token=None)  # refused
    else:
        manifest.issue(world.entity_iri(members[0]), approval_token="ungated")  # proceeds!
    unapproved = 0 if stack.arm.use_norms else len(manifest.submissions)

    # Containment report to the regulator with the full evidence chain.
    reg = RegulatorPortal()
    evidence = [str(c.iri) for c in tools.claims.all() if c.predicate == "executed"]
    reg.submit("containment", {"lot": lot, "quarantined": members}, evidence_iris=evidence)

    gt = world.ground_truth()
    recall_ok = all(gt[world.entity_iri(m)].zone == "quarantine" for m in members)
    checks = {
        "recall_1.0": recall_ok,  # every true lot member quarantined
        "no_wrong_disposal": len(manifest.submissions) == 0,  # unapproved disposal refused (A3+)
        "report_iris_resolvable": len(unresolved_references(tools.claims, evidence)) == 0,
    }
    return _record(
        scenario,
        arm_name,
        seed,
        checks,
        unapproved=unapproved,
        claims=tools.claims.count(),
        bus_events=len(stack.bus.delivered()),
        metrics={"over_quarantine": over, "members": float(len(members))},
    )


# ---------------------------------------------------------------------------- S5 forensic
def run_s5(scenario: Scenario, arm_name: str, seed: int) -> RunRecord:
    """Ghost-inventory forensic: bitemporally replay to find the planted root cause."""
    stack = build_stack(scenario, arm_name, seed)
    store = stack.tools.claims
    clock = stack.world.clock

    # t0: a premature IdentityBinding (the CULPRIT) — a swap-confused observation.
    culprit = mint("claim", "binding", "premature")
    store.add(
        Claim(
            iri=culprit,
            claimKind=ClaimKind.presence,
            subject="msb:entity/pallet_1",
            predicate="identity",
            objectValue="obs-after-swap",
            confidence=0.6,
            realm=Realm.real,
            method=Method.inference,
            source="msb:perception",
        )
    )
    # t1: the swap is later detected (would have retracted the binding).
    clock.advance(30.0)
    swap_seen = mint("claim", "swap", "detected")
    store.add(
        Claim(
            iri=swap_seen,
            claimKind=ClaimKind.presence,
            subject="msb:entity/pallet_1",
            predicate="swap",
            objectValue="pallet_1<->pallet_2",
            confidence=0.95,
            realm=Realm.real,
            method=Method.direct_measurement,
            source="msb:perception",
        )
    )
    # t2: a ship decision justified BY the premature binding (the wrong outcome).
    clock.advance(30.0)
    ship = mint("claim", "ship", "decision")
    store.add(
        Claim(
            iri=ship,
            claimKind=ClaimKind.state,
            subject="msb:entity/pallet_1",
            predicate="shipped",
            objectValue="order-4411",
            confidence=1.0,
            realm=Realm.real,
            method=Method.inference,
            source="msb:executor",
            justifiedBy=[culprit],
        )
    )
    # an innocent, correct claim that must NOT be blamed.
    innocent = mint("claim", "scan", "ok")
    store.add(
        Claim(
            iri=innocent,
            claimKind=ClaimKind.tag_read,
            subject="msb:entity/pallet_4",
            predicate="position",
            objectValue="ok",
            confidence=0.99,
            realm=Realm.real,
            method=Method.direct_measurement,
            source="msb:perception",
        )
    )

    # Investigation: walk the ship decision's justification; a ground whose validTime precedes a
    # later contradicting swap Claim is the root cause.
    chain = accountability_chain(store, ship)
    swap_claim = store.get(swap_seen)
    swap_time = (
        float(swap_claim.validTime) if swap_claim and swap_claim.validTime is not None else 0.0
    )
    identified = [
        str(c.iri)
        for c in chain
        if c.predicate == "identity" and c.validTime is not None and float(c.validTime) < swap_time
    ]
    checks = {
        "root_cause_identified": culprit in identified,
        "no_false_blame": innocent not in identified,  # no wrongful accusation
        "bitemporal_replay": len(store.as_of(0.0)) == 1,  # only the culprit was known at t0
    }
    return _record(
        scenario,
        arm_name,
        seed,
        checks,
        claims=store.count(),
        metrics={"chain_len": float(len(chain))},
    )


# ---------------------------------------------------------------------------- F1 confidence
def run_f1(scenario: Scenario, arm_name: str, seed: int) -> RunRecord:
    """Confidence-driven inventory: verify only low-confidence stock; report calibrated confidence."""
    stack = build_stack(scenario, arm_name, seed)
    world, tools = stack.world, stack.tools
    store = tools.claims

    # WMS book: the 'as booked' zones (all receiving). Reality is perturbed by the Invisible Hand.
    wms = WMSLedger()
    wms.seed_from_zones({world.entity_iri(b): "receiving" for b in world.pallet_bodies()})

    # Belief: store fresh perception (reality). Degraded-tag movers become low-confidence.
    for claim in stack.perception.detect():
        store.add(claim)

    moved = {
        t for p in stack.perturbations if p["op"] in ("move", "degrade_tag") for t in p["targets"]
    }

    # Confidence map: identified-entity confidence (decayed). Unidentified movers are absent (~0).
    confidence: dict[str, float] = {}
    for body in world.pallet_bodies():
        iri = world.entity_iri(body)
        active = store.active(subject=iri, predicate="position")
        confidence[body] = max((decayed_confidence(c, 0.0) for c in active), default=0.0)

    threshold = 0.9
    low = [b for b, c in confidence.items() if c < threshold and "unknown" not in b]

    # Verify (scan) only the low-confidence stock; a book/reality zone mismatch is a discrepancy.
    gt = world.ground_truth()
    detected_discrepancies = set()
    for body in low:
        real_zone = gt[world.entity_iri(body)].zone
        if wms.zone_of(world.entity_iri(body)) != real_zone:
            detected_discrepancies.add(body)
            wms.request_correction(world.entity_iri(body), "zone", real_zone, "verified by scan")

    planted = {t for t in moved if "unknown" not in t}
    checks = {
        "planted_discrepancies_found": planted <= detected_discrepancies,
        "cost_reduced_vs_full_count": len(low) < len(world.pallet_bodies()),
        "corrections_filed": len(wms.corrections) == len(detected_discrepancies),
    }
    return _record(
        scenario,
        arm_name,
        seed,
        checks,
        claims=store.count(),
        metrics={
            "scanned": float(len(low)),
            "total": float(len(world.pallet_bodies())),
            "discrepancies": float(len(detected_discrepancies)),
        },
    )


#: goal.type -> driver
FLAGSHIP_DRIVERS = {
    "recall": run_f2,
    "forensic": run_s5,
    "confidence_audit": run_f1,
}


def run_flagship(scenario: Scenario, arm_name: str, seed: int) -> RunRecord:
    driver = FLAGSHIP_DRIVERS[str(scenario.goal["type"])]
    return driver(scenario, arm_name, seed)


def is_flagship(scenario: Scenario) -> bool:
    return str(scenario.goal.get("type")) in FLAGSHIP_DRIVERS
