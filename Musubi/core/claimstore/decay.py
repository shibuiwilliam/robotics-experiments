"""Confidence decay — belief loses confidence over time at a claim-kind-specific half-life.

Half-lives live in ``config/registry.yaml`` (claims.decay_half_life_s): position/pose decay in
minutes, ownership over a day, etc. (PROJECT.md §8: Confidence Decay). Decayed confidence is the
input to belief mediation, never a stored mutation of the Claim.
"""

from __future__ import annotations

from typing import Any

from config import load_registry


def half_life_for(claim_kind: str) -> float:
    """Half-life (seconds) for a claim kind, from the registry (falls back to `default`)."""
    reg = load_registry()
    table = reg.require("claims.decay_half_life_s")
    if claim_kind in table:
        return float(table[claim_kind])
    return float(table["default"])


def decayed_confidence(claim: Any, at_time: float) -> float:
    """Confidence of ``claim`` decayed to ``at_time`` (sim seconds).

    ``decayed = confidence * 0.5 ** (elapsed / half_life)``. Before validTime (or if confidence
    is unset) the base confidence is returned unchanged. Never negative; never grows.
    """
    base = getattr(claim, "confidence", None)
    if base is None:
        return 0.0
    valid = getattr(claim, "validTime", None)
    if valid is None:
        return float(base)
    elapsed = at_time - float(valid)
    if elapsed <= 0:
        return float(base)
    hl = half_life_for(getattr(claim, "claimKind", "default"))
    if hl <= 0:
        return float(base)
    return float(base) * (0.5 ** (elapsed / hl))
