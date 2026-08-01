"""Musubi tool implementations (core-backed)."""

from __future__ import annotations

from dataclasses import dataclass

from core.claimstore import ClaimStore
from core.mediator import Mediator
from core.norms import Gate, NormStore
from core.registry import CapabilityRegistry, EntityRegistry
from ontology.generated.musubi_types import Action


@dataclass
class ClaimAnswer:
    """The mediated answer to a claim query: winning value + confidence + source claim IRI."""

    subject: str
    predicate: str
    value: str | None
    confidence: float
    claim_iri: str | None


class MusubiTools:
    """The agent's typed access to belief, identity, norms, and the gate."""

    def __init__(
        self,
        *,
        claims: ClaimStore,
        entities: EntityRegistry,
        capabilities: CapabilityRegistry,
        norms: NormStore,
        mediator: Mediator | None = None,
    ) -> None:
        self.claims = claims
        self.entities = entities
        self.capabilities = capabilities
        self.norms = norms
        self.mediator = mediator or Mediator(claims, entities)
        self.gate = Gate(norms)

    # -- entity_resolve ------------------------------------------------------
    def entity_resolve(self, business_key: str) -> str | None:
        """Resolve a business key / tag to an entity IRI (None if unknown)."""
        iri = (
            business_key if business_key.startswith("msb:entity/") else f"msb:entity/{business_key}"
        )
        if self.entities.get(iri) is not None:
            return iri
        # fall back to any claim subject naming this entity
        for claim in self.claims.all():
            if str(claim.subject) == iri:
                return iri
        return None

    # -- claim_query ---------------------------------------------------------
    def claim_query(self, subject: str, predicate: str, at_time: float = 0.0) -> ClaimAnswer:
        """Return the mediated best belief for (subject, predicate)."""
        active = self.claims.active(subject=subject, predicate=predicate)
        if not active:
            return ClaimAnswer(subject, predicate, None, 0.0, None)
        outcome = self.mediator.resolve(subject, predicate, at_time)
        winner = outcome.winner if outcome is not None else active[0]
        return ClaimAnswer(
            subject=subject,
            predicate=predicate,
            value=None if winner.objectValue is None else str(winner.objectValue),
            confidence=float(self.claims.decayed_confidence(winner, at_time)),
            claim_iri=str(winner.iri),
        )

    # -- norm_check ----------------------------------------------------------
    def norm_check(
        self, action_type: str, context: dict[str, str], active_regimes: set[str] | None = None
    ) -> list[str]:
        """Return the IRIs of prohibitions that would fire for an action in a context."""
        ctx = dict(context)
        ctx.setdefault("actionType", action_type)
        from core.norms.store import norm_applies

        return [str(n.iri) for n in self.norms.prohibitions(active_regimes) if norm_applies(n, ctx)]

    # -- plan_validate -------------------------------------------------------
    def plan_validate(
        self,
        actions: list[Action],
        *,
        approvals: frozenset[str] = frozenset(),
        action_context: dict[str, dict[str, str]] | None = None,
        world_doc: object = None,
        active_regimes: set[str] | None = None,
    ) -> object:
        """Run a plan through the verification gate. Returns a GateResult."""
        return self.gate.check_plan(
            actions,
            approvals=approvals,
            action_context=action_context,
            world_doc=world_doc,
            active_regimes=active_regimes,
        )
