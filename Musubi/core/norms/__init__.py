"""Norms + verification gate (FR-NORM, FR-GATE).

A Norm is an obligation / permission / prohibition with scope, strength, source clause, and (for
regime switches, C2) an overlay priority. The Gate runs a Plan through: SHACL (world_ok) → norm
check (prohibitions) → reversibility gate (irreversible needs approval) → resource reservation.
The "unapproved irreversible = 0" invariant is a must-pass (CLAUDE.md §12).
"""

from __future__ import annotations

from core.norms.gate import Gate, GateResult, Violation
from core.norms.store import NormStore, norm_applies

__all__ = ["Gate", "GateResult", "Violation", "NormStore", "norm_applies"]
