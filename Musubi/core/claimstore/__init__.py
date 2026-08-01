"""Claim store — append-only, bitemporal belief storage with confidence decay.

The store never mutates or deletes a Claim (CLAUDE.md §0-3). Replacement is via ``supersede``,
which appends a new Claim referencing the old one's IRI. "Active" claims are simply those not
referenced by any other Claim's ``supersedes``. This makes bitemporal forensics (S5) possible:
you can always ask "what did the system believe at transaction-time T?".
"""

from __future__ import annotations

from core.claimstore.decay import decayed_confidence, half_life_for
from core.claimstore.store import ClaimStore

__all__ = ["ClaimStore", "decayed_confidence", "half_life_for"]
