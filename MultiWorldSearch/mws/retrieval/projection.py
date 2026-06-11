"""Consumer-aware projection — same atom, different view per consumer."""

from __future__ import annotations

from typing import Any

from mws.core.atom import ExperienceAtom
from mws.core.types import ConsumerType


def project_for_consumer(
    atom: ExperienceAtom,
    consumer: ConsumerType,
) -> dict[str, Any]:
    """Project an atom into a consumer-appropriate format.

    - VLA: pose + tensor data + payload
    - LLM: text summary + provenance citation
    - CONTROL_LOOP: numeric values, low overhead
    - DASHBOARD: aggregated summary
    - AUDIT: full provenance chain
    """
    base = {
        "atom_id": atom.atom_id,
        "modality": atom.modality,
        "timestamp": atom.coord.timestamp,
    }

    if consumer == ConsumerType.VLA:
        return {
            **base,
            "position": (atom.coord.x, atom.coord.y, atom.coord.z),
            "payload": atom.payload,
            "tags": atom.tags,
        }
    elif consumer == ConsumerType.LLM:
        origin = atom.provenance.origin
        citation = f"[{origin.source_type}:{origin.source_id}]" if origin else "[unknown]"
        return {
            **base,
            "text": atom.text_summary,
            "citation": citation,
            "trust": atom.trust,
            "tags": atom.tags,
        }
    elif consumer == ConsumerType.CONTROL_LOOP:
        return {
            **base,
            "values": atom.structured_fields,
            "position": (atom.coord.x, atom.coord.y, atom.coord.z),
        }
    elif consumer == ConsumerType.DASHBOARD:
        return {
            **base,
            "summary": atom.text_summary[:200],
            "trust": atom.trust,
            "freshness": atom.freshness,
        }
    elif consumer == ConsumerType.AUDIT:
        return {
            **base,
            "text": atom.text_summary,
            "provenance": [r.model_dump() for r in atom.provenance.records],
            "trust": atom.trust,
            "access": atom.access.model_dump(),
        }
    return base
