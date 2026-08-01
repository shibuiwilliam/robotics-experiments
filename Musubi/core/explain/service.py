"""Explanation service implementation."""

from __future__ import annotations

from collections.abc import Iterable

from core.claimstore import ClaimStore
from ontology.generated.musubi_types import Claim


def accountability_chain(store: ClaimStore, claim_iri: str) -> list[Claim]:
    """Walk ``justifiedBy`` from a Claim back to its grounds (breadth-first, deduplicated).

    Returns the reachable Claims (including the start) in discovery order. Cycles are broken.
    """
    seen: set[str] = set()
    order: list[Claim] = []
    frontier: list[str] = [claim_iri]
    while frontier:
        current = frontier.pop(0)
        if current in seen:
            continue
        seen.add(current)
        claim = store.get(current)
        if claim is None:
            continue
        order.append(claim)
        for ref in getattr(claim, "justifiedBy", None) or []:
            if str(ref) not in seen:
                frontier.append(str(ref))
    return order


def unresolved_references(store: ClaimStore, iris: Iterable[str]) -> set[str]:
    """The subset of ``iris`` that do NOT resolve to a stored Claim (IRI-resolvability oracle)."""
    return {iri for iri in iris if store.get(iri) is None}


def trace_completeness(store: ClaimStore, action_claim_iris: Iterable[str]) -> float:
    """Fraction of action Claims whose accountability chain reaches a business ground.

    A "ground" is a Claim whose method is ``ledger_of_record`` or which has no further
    ``justifiedBy`` (a root). Used for the ≥95% trace-completeness gate (Phase 0-1 DoD).
    """
    action_iris = list(action_claim_iris)
    if not action_iris:
        return 1.0
    grounded = 0
    for iri in action_iris:
        chain = accountability_chain(store, iri)
        if any(_is_ground(c) for c in chain):
            grounded += 1
    return grounded / len(action_iris)


def _is_ground(claim: Claim) -> bool:
    method = str(getattr(claim.method, "value", claim.method)) if claim.method else ""
    if method == "ledger_of_record":
        return True
    return not (getattr(claim, "justifiedBy", None) or [])


def explain_claim(store: ClaimStore, claim_iri: str) -> str:
    """A human-readable accountability explanation for a Claim (drill-down, F1)."""
    chain = accountability_chain(store, claim_iri)
    if not chain:
        return f"{claim_iri}: (no such claim)"
    lines = [f"Explanation for {claim_iri}:"]
    for depth, claim in enumerate(chain):
        method = str(getattr(claim.method, "value", claim.method)) if claim.method else "?"
        lines.append(
            f"  {'  ' * depth}- {claim.iri} [{claim.predicate}={claim.objectValue}] "
            f"by {claim.source} via {method} (conf={claim.confidence})"
        )
    return "\n".join(lines)
