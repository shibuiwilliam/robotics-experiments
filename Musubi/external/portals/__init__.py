"""Outward-facing portals (audit / regulator / CRM / disposal manifest) — in-process mocks.

These are the "others who demand an explanation" (Scenario Catalog principle 4): they receive
reports, allow drill-down, and (for disposal) gate an irreversible external action. Submissions are
retained so oracles can check report completeness + IRI-resolvability.
"""

from __future__ import annotations

from external.portals.portals import (
    AuditPortal,
    CRMPortal,
    DisposalManifest,
    RegulatorPortal,
    Submission,
)

__all__ = ["AuditPortal", "RegulatorPortal", "CRMPortal", "DisposalManifest", "Submission"]
