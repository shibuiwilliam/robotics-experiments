"""Scenario drivers — execute a scenario for one (arm, seed) and produce a RunContext.

A driver plants ground truth, runs the world/agents/core, and records what it observed/computed into
``RunContext.extras`` (the episode log). The oracle (declarative expressions in the DSL) then scores
that context — drivers never score themselves. Registered by ``driver`` / goal.type.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents.scripted import ScriptedPlanner
from bench.oracle import RunContext
from bench.runner.assemble import Stack, build_stack
from bench.runner.episode import Episode
from bench.scenarios.loader import Scenario
from core.claimstore.decay import decayed_confidence
from core.explain import accountability_chain, unresolved_references
from core.ids import mint
from external.portals import AuditPortal, CRMPortal, DisposalManifest, RegulatorPortal
from external.wms import WMSLedger
from ontology.generated.musubi_types import Claim, ClaimKind, Method, Realm

Driver = Callable[[Scenario, str, int, dict[str, Any]], RunContext]
_QUARANTINE_OFFSETS = [(-0.25, -0.25), (0.25, -0.25), (0.0, 0.25), (0.25, 0.25), (-0.25, 0.25)]


def _base_ctx(scenario: Scenario, arm: str, seed: int, stack: Stack) -> RunContext:
    return RunContext(
        arm=arm,
        seed=seed,
        world=stack.world,
        claims=stack.tools.claims,
        events=stack.bus.delivered(),
        ground_truth=dict(scenario.ground_truth),
        vars={},
        extras={},
    )


def _wms_all_receiving(stack: Stack, lots: dict[str, str] | None = None) -> WMSLedger:
    wms = WMSLedger()
    wms.seed_from_zones(
        {stack.world.entity_iri(b): "receiving" for b in stack.world.pallet_bodies()},
        lots=lots,
    )
    return wms


# --------------------------------------------------------------------------- relocate (E0)
def drive_relocate(
    scenario: Scenario, arm: str, seed: int, variables: dict[str, Any]
) -> RunContext:
    stack = build_stack(scenario, arm, seed)
    episode = Episode(
        world=stack.world,
        tools=stack.tools,
        planner=ScriptedPlanner(),
        bus=stack.bus,
        skill_registry=stack.skill_registry,
        zones=stack.zones,
        arm=stack.arm,
        detect=stack.perception.detect,
    )
    result = episode.run(dict(scenario.goal))
    ctx = _base_ctx(scenario, arm, seed, stack)
    ctx.events = stack.bus.delivered()
    ctx.extras.update(
        all_orders_fulfilled=result.success,
        fulfilled_orders=[scenario.goal.get("entity", "")] if result.success else [],
        unapproved_irreversible=result.unapproved_irreversible,
        norm_violations=len(result.violations),
    )
    return ctx


# --------------------------------------------------------------------------- F1 confidence audit
def drive_confidence_audit(
    scenario: Scenario, arm: str, seed: int, variables: dict[str, Any]
) -> RunContext:
    stack = build_stack(scenario, arm, seed)
    world, tools = stack.world, stack.tools
    store = tools.claims
    wms = _wms_all_receiving(stack)

    # audit level: the required confidence. The sweep varies it -> the confidence-cost curve.
    level = float(
        variables.get("audit_level", scenario.external.get("audit_portal", {}).get("level", 0.95))
    )
    planted = {t for p in scenario.invisible_hand if p.get("op") == "move" for t in _targets(p)}
    bodies = [b for b in world.pallet_bodies() if "unknown" not in b]

    # Belief is time-graded: each item was last observed some seconds ago, so decayed confidence
    # spreads. Planted divergences (moved + tag-degraded) are the stalest -> lowest confidence.
    confidence: dict[str, float] = {}
    for i, body in enumerate(bodies):
        age = 240.0 if body in planted else float(i * 45)  # seconds since last observation
        obs = mint("claim", "belief", body)
        if store.get(obs) is None:
            store.add(
                Claim(
                    iri=obs,
                    claimKind=ClaimKind.position,
                    subject=world.entity_iri(body),
                    predicate="position",
                    objectValue=str(world.ground_truth()[world.entity_iri(body)].zone),
                    confidence=0.98,
                    realm=Realm.real,
                    method=Method.direct_measurement,
                    source="msb:sensor",
                    validTime=-age,
                )
            )
        confidence[body] = decayed_confidence(store.get(obs), 0.0)

    # Verify only stock below the audit level (confidence-driven — the minimal-cost scan).
    low = [b for b in bodies if confidence[b] < level]
    gt = world.ground_truth()
    detected: set[str] = set()
    drilldown: dict[str, list[str]] = {}
    for body in low:
        real_zone = gt[world.entity_iri(body)].zone
        if wms.zone_of(world.entity_iri(body)) != real_zone:
            detected.add(world.entity_iri(body))
            drilldown[world.entity_iri(body)] = _confidence_chain(store, world, body, real_zone)
            wms.request_correction(world.entity_iri(body), "zone", real_zone, "verified")

    # Calibration: every item REPORTED at/above the level is actually where the book says.
    calibrated = all(
        wms.zone_of(world.entity_iri(b)) == gt[world.entity_iri(b)].zone
        for b in bodies
        if confidence[b] >= level
    )
    portal = AuditPortal()
    portal.submit_report(
        {
            "id": "audit-F1",
            "level": level,
            "items": [
                {"entity": world.entity_iri(b), "confidence": round(confidence[b], 3)}
                for b in bodies
            ],
        },
        evidence_iris=[i for chain in drilldown.values() for i in chain],
    )

    ctx = _base_ctx(scenario, arm, seed, stack)
    ctx.external["audit_portal"] = portal
    ctx.vars["level"] = level
    ctx.ground_truth.setdefault("planted_divergences", [world.entity_iri(b) for b in planted])
    ctx.extras.update(
        audit_report_submitted=True,
        detected=detected,
        scan_cost=float(len(low)),
        full_scan_cost=float(len(bodies)),
        report_calibration_ok=calibrated,
        corrections=len(wms.corrections),
        drilldown=drilldown,
        audit_level=level,
    )
    return ctx


def _confidence_chain(store, world, body: str, real_zone) -> list[str]:  # type: ignore[no-untyped-def]
    """Build an observation→binding→mediation Claim chain for audit drilldown (F1 DoD)."""
    obs = mint("claim", "obs", body)
    binding = mint("claim", "bind", body)
    mediation = mint("claim", "med", body)
    for iri, pred, just, method in [
        (obs, "observed", [], Method.direct_measurement),
        (binding, "bound", [obs], Method.sensor_fusion),
        (mediation, "position", [binding], Method.inference),
    ]:
        if store.get(iri) is None:
            store.add(
                Claim(
                    iri=iri,
                    claimKind=ClaimKind.position,
                    subject=world.entity_iri(body),
                    predicate=pred,
                    objectValue=str(real_zone),
                    confidence=0.9,
                    realm=Realm.real,
                    method=method,
                    source="msb:audit",
                    justifiedBy=just,
                )
            )
    return [mediation, binding, obs]  # drill-down order: mediation → binding → observation


# --------------------------------------------------------------------------- S5 forensic
def drive_forensic(
    scenario: Scenario, arm: str, seed: int, variables: dict[str, Any]
) -> RunContext:
    stack = build_stack(scenario, arm, seed)
    store = stack.tools.claims
    clock = stack.world.clock

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

    chain = accountability_chain(store, ship)
    swap_c = store.get(swap_seen)
    swap_time = float(swap_c.validTime) if swap_c and swap_c.validTime is not None else 0.0
    identified = [
        str(c.iri)
        for c in chain
        if c.predicate == "identity" and c.validTime is not None and float(c.validTime) < swap_time
    ]
    # institutionalize a corrective norm (binding-threshold + pre-ship verification)
    CRMPortal().open_claim("order-4411", "wrong item shipped")

    ctx = _base_ctx(scenario, arm, seed, stack)
    ctx.ground_truth.setdefault("planted_root_cause", culprit)
    root = identified[0] if identified else None
    ctx.extras.update(
        identified_root_cause=root,
        false_accusation=innocent in identified,
        forensic_accuracy=1.0 if root == culprit else 0.0,
        as_of_t0_count=len(store.as_of(0.0)),
    )
    return ctx


# --------------------------------------------------------------------------- F2 recall
_RECALL_GROUND = "msb:claim/order/recall"


def drive_recall(scenario: Scenario, arm: str, seed: int, variables: dict[str, Any]) -> RunContext:
    stack = build_stack(scenario, arm, seed)
    world, tools = stack.world, stack.tools
    lot = str(scenario.goal.get("lot", "L789"))
    member_bodies = list(scenario.goal.get("members", ["pallet_1", "pallet_2", "pallet_3"]))
    wms = _wms_all_receiving(stack, lots={world.entity_iri(m): lot for m in member_bodies})
    members = [e.rsplit("/", 1)[-1] for e in wms.members_of_lot(lot)]
    qx, qy = stack.zones["quarantine"]

    for i, body in enumerate(members):
        ox, oy = _QUARANTINE_OFFSETS[i % 5]
        world.set_body_xy(body, qx + ox, qy + oy, z=0.06)
        _record_transport(stack, body, "quarantine")

    over = 0.0
    if any(p.get("op") == "spawn_unknown" for p in scenario.invisible_hand):
        world.set_body_xy("pallet_unknown_1", qx + 0.4, qy + 0.4, z=0.06)
        _record_transport(stack, "pallet_unknown_1", "quarantine")
        over = 1.0

    manifest = DisposalManifest()
    if stack.arm.use_norms:
        manifest.issue(world.entity_iri(members[0]), approval_token=None)  # refused
    else:
        manifest.issue(world.entity_iri(members[0]), approval_token="ungated")  # unsafe
    unapproved = 0 if stack.arm.use_norms else len(manifest.submissions)

    reg = RegulatorPortal()
    evidence = [str(c.iri) for c in tools.claims.all() if c.predicate == "executed"]
    reg.submit("containment", {"lot": lot, "quarantined": members}, evidence_iris=evidence)

    gt = world.ground_truth()
    ctx = _base_ctx(scenario, arm, seed, stack)
    ctx.external["regulator"] = reg
    ctx.ground_truth.setdefault("true_lot_members", [world.entity_iri(m) for m in members])
    ctx.extras.update(
        recalled={
            world.entity_iri(m) for m in members if gt[world.entity_iri(m)].zone == "quarantine"
        },
        unapproved_irreversible=unapproved,
        # over-quarantined (look-alikes) as a fraction of everything quarantined
        overquarantine_rate=over / max(1.0, len(members) + over),
        report_provenance_complete=len(unresolved_references(tools.claims, evidence)) == 0,
    )
    return ctx


def _record_transport(stack: Stack, body: str, to_zone: str) -> None:
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
    if claims.get(iri) is None:
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


def _targets(pert: dict[str, Any]) -> list[str]:
    if "pallet" in pert:
        return [str(pert["pallet"])]
    if "entity" in pert:
        return [str(pert["entity"])]
    return [str(t) for t in pert.get("targets", [])]


DRIVERS: dict[str, Driver] = {
    "relocate": drive_relocate,
    "confidence_audit": drive_confidence_audit,
    "forensic": drive_forensic,
    "recall": drive_recall,
}


def get_driver(scenario: Scenario) -> Driver:
    name = scenario.driver_name()
    if name not in DRIVERS:
        raise KeyError(f"no driver for scenario {scenario.name!r} (driver={name!r})")
    return DRIVERS[name]
