"""Portal mocks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Submission:
    """A report submitted to a portal, with its evidence IRIs (for completeness oracles)."""

    kind: str
    payload: dict[str, Any]
    evidence_iris: list[str] = field(default_factory=list)


class _PortalBase:
    def __init__(self, name: str) -> None:
        self.name = name
        self._submissions: list[Submission] = []

    def submit(
        self, kind: str, payload: dict[str, Any], evidence_iris: list[str] | None = None
    ) -> Submission:
        s = Submission(kind=kind, payload=payload, evidence_iris=list(evidence_iris or []))
        self._submissions.append(s)
        return s

    @property
    def submissions(self) -> list[Submission]:
        return list(self._submissions)


class AuditPortal(_PortalBase):
    """Audit firm portal (F1): receives the confidence report; supports per-item drill-down."""

    def __init__(self) -> None:
        super().__init__("audit")
        self._reports: dict[str, dict[str, Any]] = {}

    def submit_report(self, report: dict[str, Any], evidence_iris: list[str]) -> Submission:
        self._reports[str(report.get("id", "report"))] = report
        return self.submit("confidence_report", report, evidence_iris)

    def drilldown(self, item: str) -> dict[str, Any] | None:
        for report in self._reports.values():
            for row in report.get("items", []):
                if row.get("entity") == item:
                    return row
        return None


class RegulatorPortal(_PortalBase):
    """Regulator portal (F2): receives containment reports with a full evidence chain."""

    def __init__(self) -> None:
        super().__init__("regulator")


class CRMPortal(_PortalBase):
    """Customer complaint portal (S5): opens a claim to investigate."""

    def __init__(self) -> None:
        super().__init__("crm")

    def open_claim(self, order: str, complaint: str) -> Submission:
        return self.submit("claim", {"order": order, "complaint": complaint})


class DisposalManifest(_PortalBase):
    """Disposal contractor (F2): issuing a manifest is an IRREVERSIBLE external action.

    Requires an approval token — this models the irreversible gate at the world boundary.
    """

    def __init__(self) -> None:
        super().__init__("disposal")

    def issue(self, entity: str, approval_token: str | None) -> Submission | None:
        if not approval_token:
            return None  # refused: no approval -> no irreversible disposal
        return self.submit("manifest", {"entity": entity, "approved_by": approval_token})
