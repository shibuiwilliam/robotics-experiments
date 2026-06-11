"""Provenance chain model — tracks origin and lineage of atoms."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class ProvenanceRecord(BaseModel):
    """Single entry in an atom's provenance chain."""

    source_id: str = Field(description="ID of the producing entity/sensor/system")
    source_type: str = Field(description="Type: sensor, agent, system, human, consolidation")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: str = Field(description="What happened: created, transformed, merged, projected")
    details: dict[str, str] = Field(default_factory=dict)


class ProvenanceChain(BaseModel):
    """Ordered chain of provenance records for an atom."""

    records: list[ProvenanceRecord] = Field(default_factory=list)

    def add(
        self,
        source_id: str,
        source_type: str,
        action: str,
        details: dict[str, str] | None = None,
    ) -> None:
        self.records.append(
            ProvenanceRecord(
                source_id=source_id,
                source_type=source_type,
                action=action,
                details=details or {},
            )
        )

    @property
    def origin(self) -> ProvenanceRecord | None:
        """The first (creating) record."""
        return self.records[0] if self.records else None
