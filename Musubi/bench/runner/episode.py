"""Episode — execute one plan against the world + core at a given ablation arm.

This is the vertical slice that ties every module together: perceive → (mediate) → plan →
(gate) → execute → (explain). Metrics returned here feed the scoreboard; the god-view oracle
scores success separately (never inside the system path).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.planner import Plan, Planner
from agents.tools import MusubiTools
from bench.runner.arm import Arm
from core.bus import EventBus, make_envelope
from core.claimstore import ClaimStore
from core.explain import trace_completeness
from core.ids import mint
from ontology.generated.musubi_types import Claim, ClaimKind, Method, Realm
from sim.skills import Skill
from sim.world import World


def _parse_xyz(value: str | None) -> tuple[float, float, float] | None:
    if not value:
        return None
    try:
        x, y, z = (float(v) for v in str(value).split(","))
        return x, y, z
    except ValueError:
        return None


@dataclass
class EpisodeResult:
    goal: dict[str, Any]
    arm: str
    case_iri: str
    plan_steps: int
    executed: int
    success: bool
    gate_ok: bool
    unapproved_irreversible: int
    trace_completeness: float
    claims: int
    bus_events: int
    api_calls: int
    violations: list[str] = field(default_factory=list)
    reason: str = ""


class Episode:
    """Runs one goal to completion under an arm's feature set."""

    def __init__(
        self,
        *,
        world: World,
        tools: MusubiTools,
        planner: Planner,
        bus: EventBus,
        skill_registry: dict[str, type[Skill]],
        zones: dict[str, tuple[float, float]],
        arm: Arm,
        detect: Any = None,  # callable -> list[Claim] (perception pass), used when use_claims
        business_ground: str = "msb:claim/order/E0",
    ) -> None:
        self._world = world
        self._tools = tools
        self._planner = planner
        self._bus = bus
        self._skills = skill_registry
        self._zones = zones
        self._arm = arm
        self._detect = detect
        self._ground = business_ground
        self._action_claims: list[str] = []

    def run(self, goal: dict[str, Any]) -> EpisodeResult:
        case_iri = mint(
            "case", str(goal.get("entity", "e")).rsplit("/", 1)[-1], goal.get("to_zone", "z")
        )
        store = self._tools.claims

        # 0) business ground (the order that justifies everything) — the accountability root.
        if self._arm.use_claims:
            self._seed_ground(store, goal)

        # 1) perceive. A2+ store detections as Claims (belief layer); all arms keep a direct read
        #    (bare coupling reads the sensor directly, no Claim abstraction).
        detections = self._detect() if self._detect is not None else []
        direct: dict[str, tuple[float, float, float]] = {}
        for claim in detections:
            if self._arm.use_claims:
                store.add(claim)
                self._emit(case_iri, "Detected", {"subject": str(claim.subject)})
            xyz = _parse_xyz(claim.objectValue)
            if xyz is not None and str(claim.subject).startswith("msb:entity/"):
                direct[str(claim.subject)] = xyz

        # 2) plan
        bot = self._world.body_pos("lift_bot")
        ctx = {
            "tools": self._tools,
            "zones": self._zones,
            "at_time": self._world.clock.now(),
            "bot_xy": (float(bot[0]), float(bot[1])),
            "direct_positions": direct,
        }
        try:
            plan = self._planner.plan(goal, ctx)
        except Exception as exc:  # planning failure is a domain outcome, not a crash
            return self._fail(goal, case_iri, 0, f"planning_failed: {exc}")

        # 3) gate (A3+)
        gate_ok, violations, unapproved = True, [], 0
        if self._arm.use_norms:
            result = self._tools.plan_validate(
                plan.actions(),
                approvals=plan.approvals(),
                action_context=plan.action_context(),
            )
            gate_ok = bool(result.ok)  # type: ignore[attr-defined]
            violations = [v.kind for v in result.violations]  # type: ignore[attr-defined]
            unapproved = result.unapproved_irreversible()  # type: ignore[attr-defined]
            if not gate_ok:
                return self._fail(goal, case_iri, 0, "gate_rejected", violations, unapproved)

        # 4) execute
        executed = self._execute(plan, case_iri)

        # 5) explain (A4)
        completeness = (
            trace_completeness(store, self._action_claims) if self._arm.use_explain else 1.0
        )

        success = executed == len(plan.steps) and self._goal_reached(goal)
        return EpisodeResult(
            goal=goal,
            arm=self._arm.name,
            case_iri=case_iri,
            plan_steps=len(plan.steps),
            executed=executed,
            success=success,
            gate_ok=gate_ok,
            unapproved_irreversible=unapproved,
            trace_completeness=completeness,
            claims=store.count(),
            bus_events=len(self._bus.delivered()),
            api_calls=0,  # oracle dial: no cloud calls
            violations=violations,
            reason="ok" if success else "goal_not_reached",
        )

    # -- helpers -------------------------------------------------------------
    def _seed_ground(self, store: ClaimStore, goal: dict[str, Any]) -> None:
        if store.get(self._ground) is None:
            store.add(
                Claim(
                    iri=self._ground,
                    claimKind=ClaimKind.ownership,
                    subject=str(goal.get("entity", "msb:e/unknown")),
                    predicate="order",
                    objectValue=f"relocate->{goal.get('to_zone')}",
                    confidence=1.0,
                    realm=Realm.real,
                    method=Method.ledger_of_record,
                    source="msb:wms",
                )
            )

    def _execute(self, plan: Plan, case_iri: str) -> int:
        executed = 0
        for step in plan.steps:
            skill_cls = self._skills.get(step.action_type)
            if skill_cls is None:
                break  # no capability for this action type
            skill = skill_cls(**step.params)
            outcome = skill.execute(self._world)
            self._emit(
                case_iri, "SkillExecuted", {"action": str(step.action.iri), "ok": outcome.success}
            )
            if self._arm.use_claims:
                self._record_action_claim(step, outcome.success)
            if not outcome.success:
                break
            executed += 1
        return executed

    def _record_action_claim(self, step: Any, ok: bool) -> None:
        iri = mint("claim", "act", str(step.action.iri).rsplit("/", 1)[-1])
        self._tools.claims.add(
            Claim(
                iri=iri,
                claimKind=ClaimKind.state,
                subject=str(step.action.iri),
                predicate="executed",
                objectValue=str(ok),
                confidence=1.0,
                realm=Realm.real,
                method=Method.inference,
                source="msb:executor",
                justifiedBy=[self._ground],  # every action traces to the order (NFR-TRACE)
            )
        )
        self._action_claims.append(iri)

    def _emit(self, case_iri: str, event_type: str, payload: dict[str, Any]) -> None:
        if not self._arm.use_envelopes:
            return
        self._bus.publish(
            make_envelope(
                event_type,
                payload,
                id=mint("ev", event_type, str(len(self._bus.log))),
                trace=case_iri,
                sim_time=self._world.clock.now(),
                source="msb:executor",
            )
        )

    def _goal_reached(self, goal: dict[str, Any]) -> bool:
        body = str(goal["entity"]).rsplit("/", 1)[-1]
        state = self._world.ground_truth().get(f"msb:entity/{body}")
        return state is not None and state.zone == goal.get("to_zone")

    def _fail(
        self,
        goal: dict[str, Any],
        case_iri: str,
        executed: int,
        reason: str,
        violations: list[str] | None = None,
        unapproved: int = 0,
    ) -> EpisodeResult:
        return EpisodeResult(
            goal=goal,
            arm=self._arm.name,
            case_iri=case_iri,
            plan_steps=0,
            executed=executed,
            success=False,
            gate_ok=not violations,
            unapproved_irreversible=unapproved,
            trace_completeness=0.0,
            claims=self._tools.claims.count(),
            bus_events=len(self._bus.delivered()),
            api_calls=0,
            violations=violations or [],
            reason=reason,
        )
