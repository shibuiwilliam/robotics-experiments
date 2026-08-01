"""Explanation service — accountability chains and semantic-observability traces.

Every physical action must trace back to a business ground via ``justifiedBy`` (NFR-TRACE); every
Case is one observability trace (NFR-OBS). Used by scenario oracles that demand report
completeness ("all claims IRI-resolvable", F1/F2).
"""

from __future__ import annotations

from core.explain.service import (
    accountability_chain,
    explain_claim,
    trace_completeness,
    unresolved_references,
)

__all__ = [
    "accountability_chain",
    "explain_claim",
    "trace_completeness",
    "unresolved_references",
]
