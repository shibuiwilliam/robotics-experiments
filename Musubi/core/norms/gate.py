"""The verification gate: a Plan must pass before execution (FR-GATE)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from core.norms.store import NormStore, norm_applies
from ontology.generated.musubi_types import Action


@dataclass(frozen=True)
class Violation:
    """A gate violation. ``kind`` is machine-readable; ``message`` is for explanation."""

    kind: str
    message: str
    action: str | None = None
    norm: str | None = None


@dataclass
class GateResult:
    ok: bool
    violations: list[Violation] = field(default_factory=list)

    def unapproved_irreversible(self) -> int:
        """Count of the must-be-zero violation class (CLAUDE.md §12)."""
        return sum(1 for v in self.violations if v.kind == "unapproved_irreversible")


class Gate:
    """Runs a plan's Actions through SHACL, norms, reversibility, and resource reservation."""

    def __init__(self, norms: NormStore) -> None:
        self._norms = norms

    def check_plan(
        self,
        actions: Sequence[Action],
        *,
        world_doc: Any = None,
        approvals: frozenset[str] = frozenset(),
        action_context: dict[str, dict[str, str]] | None = None,
        active_regimes: set[str] | None = None,
        resources_available: set[str] | None = None,
        resource_requests: dict[str, str] | None = None,
    ) -> GateResult:
        """Validate a plan. Returns a GateResult; ``ok`` iff no violations."""
        violations: list[Violation] = []
        action_context = action_context or {}
        resource_requests = resource_requests or {}

        # 1) SHACL world acceptance (invariant floors: privacy, claim sanity, reversibility).
        if world_doc is not None:
            from ontology import artifacts

            conforms, report = artifacts.validate_world(world_doc)
            if not conforms:
                violations.append(
                    Violation("world_not_ok", f"SHACL world_ok failed: {report[:400]}")
                )

        # 2) Norm check — an action must not trigger an active prohibition.
        prohibitions = self._norms.prohibitions(active_regimes)
        for action in actions:
            ctx = dict(action_context.get(str(action.iri), {}))
            ctx.setdefault("actionType", str(action.actionType))
            for norm in prohibitions:
                if norm_applies(norm, ctx):
                    violations.append(
                        Violation(
                            "prohibited_action",
                            f"action {action.iri} ({action.actionType}) is prohibited by {norm.iri}",
                            action=str(action.iri),
                            norm=str(norm.iri),
                        )
                    )

        # 3) Reversibility gate — irreversible needs approval; compensable needs a compensation.
        for action in actions:
            rev = str(getattr(action.reversibility, "value", action.reversibility))
            if rev == "irreversible" and str(action.iri) not in approvals:
                violations.append(
                    Violation(
                        "unapproved_irreversible",
                        f"irreversible action {action.iri} lacks approval",
                        action=str(action.iri),
                    )
                )
            if rev == "compensable" and not getattr(action, "compensation", None):
                violations.append(
                    Violation(
                        "compensable_without_saga",
                        f"compensable action {action.iri} has no registered compensation",
                        action=str(action.iri),
                    )
                )

        # 4) Resource reservation — requested resources must be available.
        if resources_available is not None:
            for action_iri, resource in resource_requests.items():
                if resource not in resources_available:
                    violations.append(
                        Violation(
                            "resource_unavailable",
                            f"action {action_iri} needs unavailable resource {resource}",
                            action=action_iri,
                        )
                    )

        return GateResult(ok=not violations, violations=violations)
