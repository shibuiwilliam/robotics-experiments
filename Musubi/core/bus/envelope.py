"""Semantic envelope — a JSON-LD message carrying the generated @context and a trace key.

The trace key is a Case/Episode IRI, threading order→motor for semantic observability (NFR-OBS).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Envelopes reference the generated context by IRI to stay light; consumers resolve it locally.
CONTEXT_IRI = "https://musubi.dev/ontology/musubi/context.jsonld"


@dataclass(frozen=True)
class Envelope:
    """An immutable semantic message. ``payload`` is a JSON-LD node (or list of nodes)."""

    id: str
    event_type: str
    payload: Any
    trace: str | None = None  # Case/Episode IRI (observability trace key)
    sim_time: float = 0.0
    source: str | None = None  # IRI of the emitting instance
    meta: dict[str, Any] = field(default_factory=dict)

    def to_jsonld(self) -> dict[str, Any]:
        """Serialize to a JSON-LD envelope dict."""
        return {
            "@context": CONTEXT_IRI,
            "@type": "Event",
            "eventType": self.event_type,
            "id": self.id,
            "trace": self.trace,
            "simTime": self.sim_time,
            "source": self.source,
            "payload": self.payload,
            "meta": self.meta,
        }


def make_envelope(
    event_type: str,
    payload: Any,
    *,
    id: str,
    trace: str | None = None,
    sim_time: float = 0.0,
    source: str | None = None,
    **meta: Any,
) -> Envelope:
    """Construct an :class:`Envelope`. ``id`` should be a deterministic IRI (core.ids.mint)."""
    return Envelope(
        id=id,
        event_type=event_type,
        payload=payload,
        trace=trace,
        sim_time=sim_time,
        source=source,
        meta=dict(meta),
    )
