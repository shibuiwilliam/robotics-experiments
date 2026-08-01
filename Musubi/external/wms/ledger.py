"""WMS ledger implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class LedgerRecord:
    """The book record for one entity: recorded zone/lot/owner (may be stale vs reality)."""

    entity: str
    zone: str | None
    lot: str | None = None
    owner: str = "acme"
    quantity: int = 1


class WMSLedger:
    """A business ledger (system of record) as a Claim source. Book state, not ground truth."""

    def __init__(self, source: str = "msb:wms") -> None:
        self.source = source
        self._records: dict[str, LedgerRecord] = {}
        self._corrections: list[dict[str, Any]] = []  # append-only correction requests

    # -- seeding -------------------------------------------------------------
    def record(self, rec: LedgerRecord) -> LedgerRecord:
        self._records[rec.entity] = rec
        return rec

    def seed_from_zones(
        self, entity_zone: dict[str, str | None], lots: dict[str, str] | None = None
    ) -> None:
        """Seed the book from an initial {entity: zone} snapshot (the 'as booked' state)."""
        lots = lots or {}
        for entity, zone in entity_zone.items():
            self.record(LedgerRecord(entity=entity, zone=zone, lot=lots.get(entity)))

    # -- queries -------------------------------------------------------------
    def get(self, entity: str) -> LedgerRecord | None:
        return self._records.get(entity)

    def zone_of(self, entity: str) -> str | None:
        rec = self._records.get(entity)
        return rec.zone if rec else None

    def members_of_lot(self, lot: str) -> list[str]:
        """Fan out a business key (lot) to the physical instances it names (F2 recall)."""
        return sorted(e for e, r in self._records.items() if r.lot == lot)

    def all(self) -> list[LedgerRecord]:
        return list(self._records.values())

    # -- corrections (append-only, mirrors Claim discipline) -----------------
    def request_correction(self, entity: str, field_name: str, new_value: Any, reason: str) -> None:
        self._corrections.append(
            {"entity": entity, "field": field_name, "new_value": new_value, "reason": reason}
        )

    @property
    def corrections(self) -> list[dict[str, Any]]:
        return list(self._corrections)
