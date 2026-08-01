"""Belief mediation implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import load_registry
from core.claimstore import ClaimStore
from core.claimstore.decay import decayed_confidence
from core.ids import mint
from core.registry import EntityRegistry
from ontology.generated.musubi_types import Claim, MediationResult, Method

_MEDIATION_PREDICATE = "__superseded_by_mediation__"


@dataclass
class MediationOutcome:
    """The result of resolving a (subject, predicate) conflict at a point in time."""

    winner: Claim
    losers: list[Claim]
    scores: dict[str, float]
    reason: str

    def to_result(self, iri: str, created_at: float) -> MediationResult:
        return MediationResult(
            iri=iri,
            winner=self.winner.iri,
            losers=[c.iri for c in self.losers],
            reason=self.reason,
            createdAt=created_at,
        )


class Mediator:
    """Adjudicates conflicting Claims by authority × decayed confidence × method rank."""

    def __init__(self, store: ClaimStore, entities: EntityRegistry | None = None) -> None:
        self._store = store
        self._entities = entities
        reg = load_registry()
        self._method_rank: dict[str, int] = dict(reg.require("claims.method_rank"))

    def _authority_weight(self, claim: Claim) -> float:
        """Weight from the source's dynamic reputation, if an authority is registered (S9)."""
        if self._entities is None:
            return 1.0
        for auth in self._entities.authorities():
            if str(auth.iri) == str(claim.source) and auth.effectiveConfidence is not None:
                return float(auth.effectiveConfidence)
        return 1.0

    def _score(self, claim: Claim, at_time: float) -> float:
        method = getattr(claim.method, "value", claim.method)
        rank = self._method_rank.get(str(method), 1)
        return self._authority_weight(claim) * decayed_confidence(claim, at_time) * rank

    def resolve(self, subject: str, predicate: str, at_time: float) -> MediationOutcome | None:
        """Pick the current belief among active Claims for (subject, predicate). None if <2 claims."""
        claims = self._store.active(subject=subject, predicate=predicate)
        if len(claims) < 2:
            return None
        scores = {str(c.iri): self._score(c, at_time) for c in claims}
        ranked = sorted(claims, key=lambda c: (scores[str(c.iri)], str(c.iri)), reverse=True)
        winner, losers = ranked[0], ranked[1:]
        reason = (
            f"winner {winner.iri} score={scores[str(winner.iri)]:.4f} "
            f"(method={_v(winner.method)}, conf={winner.confidence}); "
            f"beat {len(losers)} claim(s)"
        )
        return MediationOutcome(winner=winner, losers=losers, scores=scores, reason=reason)

    def commit(self, outcome: MediationOutcome, at_time: float) -> MediationResult:
        """Persist a resolution: supersede each loser (append-only) and record a MediationResult.

        Losing Claims are superseded by mediation-marker Claims (a distinct predicate) so that
        ``active(subject, real_predicate)`` returns only the winner, while every original Claim
        remains in the store for forensic replay (S5).
        """
        for loser in outcome.losers:
            marker = Claim(
                iri=mint(
                    "claim", "mediation", str(loser.iri).replace("msb:", "").replace("/", "-")
                ),
                claimKind=loser.claimKind,
                subject=loser.subject,
                predicate=_MEDIATION_PREDICATE,
                objectValue=str(outcome.winner.iri),
                confidence=1.0,
                realm=loser.realm,
                method=Method.inference,
                source="msb:mediator",
                justifiedBy=[outcome.winner.iri],
            )
            self._store.supersede(str(loser.iri), marker)
        result_iri = mint(
            "mediation", str(outcome.winner.iri).replace("msb:", "").replace("/", "-")
        )
        return outcome.to_result(result_iri, at_time)


def _v(value: Any) -> str:
    return str(getattr(value, "value", value))
