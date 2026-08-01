"""Norm store with regime overlays.

Provisional norm↔action binding (pending the Ontology Design doc, recorded in DECISIONS.md):
a Norm's ``scope`` is a list of tokens like ``actionType:dispose`` / ``zone:quarantine`` /
``lot:L789``. A norm *applies* to an action-context when every token it specifies is satisfied.
Regime overlays: when a regime is active, its norms (higher ``priority``) override base norms (C2).
"""

from __future__ import annotations

from ontology.generated.musubi_types import Norm


def _tokens(scope: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for tok in scope or []:
        if ":" in tok:
            key, _, val = tok.partition(":")
            out[key.strip()] = val.strip()
    return out


def norm_applies(norm: Norm, context: dict[str, str]) -> bool:
    """True if every scope token of ``norm`` is matched by ``context`` (actionType/zone/lot/…)."""
    required = _tokens(norm.scope)
    if not required:
        return True  # unscoped norm applies everywhere
    return all(context.get(k) == v for k, v in required.items())


class NormStore:
    """Holds norms; resolves the effective set given the active regimes (overlay priority)."""

    def __init__(self) -> None:
        self._norms: dict[str, Norm] = {}

    def add(self, norm: Norm) -> Norm:
        self._norms[str(norm.iri)] = norm
        return norm

    def all(self) -> list[Norm]:
        return list(self._norms.values())

    def effective(self, active_regimes: set[str] | None = None) -> list[Norm]:
        """Norms in force. Base norms (no regime) plus norms of any active regime.

        When a regime is active its norms overlay the base set; ordering by descending priority
        lets callers apply the highest-priority norm first (regime switch, C2).
        """
        active_regimes = active_regimes or set()
        chosen = [n for n in self._norms.values() if n.regime is None or n.regime in active_regimes]
        return sorted(chosen, key=lambda n: -(n.priority or 0))

    def prohibitions(self, active_regimes: set[str] | None = None) -> list[Norm]:
        return [n for n in self.effective(active_regimes) if _modality(n) == "prohibition"]

    def obligations(self, active_regimes: set[str] | None = None) -> list[Norm]:
        return [n for n in self.effective(active_regimes) if _modality(n) == "obligation"]


def _modality(norm: Norm) -> str:
    return str(getattr(norm.modality, "value", norm.modality))
