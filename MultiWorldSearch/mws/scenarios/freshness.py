"""Freshness-aware physical/record reconciliation (hypothesis H4).

Pure functions that decide whether a *fresher physical observation* should
override a *stale digital record* (e.g. a WMS quantity). These are used by the
scenarios so that "ghost inventory detected" and write-back decisions are
COMPUTED from retrieved atoms + timestamps — not hard-coded booleans.

The rule (H4): a physical observation overrides a recorded quantity only when
(a) the observation is fresher than the record, and (b) the quantities disagree.
Flip either condition (older observation, or matching counts) and the override
does not fire — i.e. this is falsifiable.
"""

from __future__ import annotations

from dataclasses import dataclass

from mws.core.atom import ExperienceAtom


def _observed_at(atom: ExperienceAtom) -> float:
    """Timestamp the atom records its information as-of."""
    sf = atom.structured_fields
    if "observed_at" in sf:
        return float(sf["observed_at"])
    return float(atom.coord.timestamp)


def _quantity(atom: ExperienceAtom) -> float | None:
    sf = atom.structured_fields
    for key in ("quantity", "physical_quantity", "observed_count"):
        if key in sf:
            return float(sf[key])
    return None


@dataclass
class ReconcileResult:
    """Outcome of reconciling a record quantity against a physical observation."""

    wms_quantity: float | None
    physical_quantity: float | None
    wms_observed_at: float | None
    physical_observed_at: float | None
    physical_is_fresher: bool
    quantities_disagree: bool
    ghost_detected: bool
    override_quantity: float | None  # what the truth should be after reconciliation


def reconcile_inventory(atoms: list[ExperienceAtom]) -> ReconcileResult:
    """Reconcile a record (source=="wms") against a physical observation
    (source=="physical") drawn from a set of retrieved atoms.

    ``ghost_detected`` is True only when a physical observation that is FRESHER
    than the WMS record reports FEWER units than the record. When it fires, the
    physical quantity is the override (the new truth).
    """
    wms_atom: ExperienceAtom | None = None
    physical_atom: ExperienceAtom | None = None

    for atom in atoms:
        source = atom.structured_fields.get("source")
        if source == "wms" and wms_atom is None:
            wms_atom = atom
        # Prefer the freshest physical observation.
        elif source == "physical" and (
            physical_atom is None or _observed_at(atom) > _observed_at(physical_atom)
        ):
            physical_atom = atom

    wms_qty = _quantity(wms_atom) if wms_atom else None
    phys_qty = _quantity(physical_atom) if physical_atom else None
    wms_ts = _observed_at(wms_atom) if wms_atom else None
    phys_ts = _observed_at(physical_atom) if physical_atom else None

    physical_is_fresher = wms_ts is not None and phys_ts is not None and phys_ts > wms_ts
    quantities_disagree = wms_qty is not None and phys_qty is not None and wms_qty != phys_qty
    # Ghost inventory: record claims more than physically present, and we trust
    # the physical observation because it is fresher.
    ghost_detected = bool(
        physical_is_fresher and wms_qty is not None and phys_qty is not None and wms_qty > phys_qty
    )
    override_quantity = phys_qty if ghost_detected else wms_qty

    return ReconcileResult(
        wms_quantity=wms_qty,
        physical_quantity=phys_qty,
        wms_observed_at=wms_ts,
        physical_observed_at=phys_ts,
        physical_is_fresher=physical_is_fresher,
        quantities_disagree=quantities_disagree,
        ghost_detected=ghost_detected,
        override_quantity=override_quantity,
    )
