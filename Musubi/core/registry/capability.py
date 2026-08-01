"""Capability registry and subsumption matching (FR-CAP).

Action types form a dotted hierarchy: ``transport`` subsumes ``transport.heavy`` subsumes
``transport.heavy.pallet``. A Capability advertising ``transport`` therefore satisfies a
Requirement for ``transport.heavy``. QoS constraints must also be met (advertised or, when a
measured QoS is supplied, the measured value — S9/C3: track record beats advertisement).
"""

from __future__ import annotations

from typing import Any

from ontology.generated.musubi_types import Capability, Requirement


def subsumes(provided: str, required: str) -> bool:
    """True if action type ``provided`` subsumes ``required`` (equal or a dotted ancestor)."""
    if provided == required:
        return True
    return required.startswith(provided + ".")


def _qos_map(items: Any) -> dict[str, float]:
    """Map a list of QuantityValue to {unit: magnitude}."""
    out: dict[str, float] = {}
    for q in items or []:
        unit = getattr(q, "unit", None)
        mag = getattr(q, "magnitude", None)
        if unit is not None and mag is not None:
            out[str(unit)] = float(mag)
    return out


def matches(
    capability: Capability,
    requirement: Requirement,
    *,
    measured_qos: Any = None,
) -> bool:
    """True if ``capability`` satisfies ``requirement`` (action-type subsumption + QoS)."""
    if not any(subsumes(str(at), str(requirement.actionType)) for at in capability.actionTypes):
        return False
    required = _qos_map(getattr(requirement, "qosConstraints", None))
    if not required:
        return True
    # measured QoS (track record) overrides advertised when provided.
    advertised = _qos_map(
        measured_qos if measured_qos is not None else getattr(capability, "qos", None)
    )
    for unit, need in required.items():
        have = advertised.get(unit)
        if have is None or have < need:
            return False
    return True


class CapabilityRegistry:
    """Advertise capabilities and match requirements against them by subsumption."""

    def __init__(self) -> None:
        self._by_actor: dict[str, list[Capability]] = {}
        self._measured: dict[str, list[Any]] = {}  # actor -> measured QoS (S9)

    def advertise(self, capability: Capability) -> Capability:
        self._by_actor.setdefault(str(capability.actor), []).append(capability)
        return capability

    def record_measured_qos(self, actor: str, qos: list[Any]) -> None:
        """Record a measured QoS profile for an actor (dynamic reputation, S9/C3)."""
        self._measured[actor] = qos

    def capabilities(self) -> list[Capability]:
        return [c for caps in self._by_actor.values() for c in caps]

    def match(self, requirement: Requirement) -> list[Capability]:
        """All advertised capabilities satisfying the requirement (empty if none)."""
        out: list[Capability] = []
        for actor, caps in self._by_actor.items():
            measured = self._measured.get(actor)
            for cap in caps:
                if matches(cap, requirement, measured_qos=measured):
                    out.append(cap)
        return out
