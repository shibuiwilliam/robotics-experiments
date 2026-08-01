"""In-process event bus with deterministic toxic mode (drop / delay / partition)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from core.bus.envelope import Envelope

Handler = Callable[[Envelope], None]


@dataclass
class ToxicConfig:
    """Deterministic bus fault injection (NFR-DEGRADE). All faults are seeded, not random."""

    drop_prob: float = 0.0
    delay_s: float = 0.0
    #: Source IRIs (or IRI prefixes) that are partitioned off — their messages never deliver.
    partitioned: set[str] = field(default_factory=set)

    def is_partitioned(self, source: str | None) -> bool:
        if source is None:
            return False
        return any(source == p or source.startswith(p) for p in self.partitioned)


@dataclass
class DeliveryRecord:
    """A log entry for observability: what was published and whether it was delivered."""

    envelope: Envelope
    delivered: bool
    reason: str = ""


class EventBus:
    """Synchronous, deterministic pub/sub over semantic envelopes.

    Subscribers are dispatched in registration order. The full publish log (delivered or dropped)
    is retained for the explanation/observability services (read-only).
    """

    def __init__(
        self, rng: np.random.Generator | None = None, toxic: ToxicConfig | None = None
    ) -> None:
        self._subscribers: dict[str, list[Handler]] = {}
        self._wildcard: list[Handler] = []
        self._rng = rng
        self._toxic = toxic or ToxicConfig()
        self._log: list[DeliveryRecord] = []

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Subscribe to an event type, or to all events with ``event_type='*'``."""
        if event_type == "*":
            self._wildcard.append(handler)
        else:
            self._subscribers.setdefault(event_type, []).append(handler)

    def publish(self, envelope: Envelope) -> bool:
        """Publish an envelope. Returns True if delivered, False if dropped by toxic mode."""
        drop_reason = self._should_drop(envelope)
        if drop_reason:
            self._log.append(DeliveryRecord(envelope, delivered=False, reason=drop_reason))
            return False
        self._log.append(DeliveryRecord(envelope, delivered=True))
        for handler in self._subscribers.get(envelope.event_type, []):
            handler(envelope)
        for handler in self._wildcard:
            handler(envelope)
        return True

    def _should_drop(self, envelope: Envelope) -> str:
        if self._toxic.is_partitioned(envelope.source):
            return "partitioned"
        if (
            self._toxic.drop_prob > 0.0
            and self._rng is not None
            and float(self._rng.random()) < self._toxic.drop_prob
        ):
            return "dropped"
        return ""

    @property
    def log(self) -> list[DeliveryRecord]:
        return list(self._log)

    def delivered(self) -> list[Envelope]:
        return [r.envelope for r in self._log if r.delivered]

    def trace(self, case_iri: str) -> list[Envelope]:
        """All delivered envelopes on a given trace (Case IRI), in publish order (NFR-OBS)."""
        return [r.envelope for r in self._log if r.delivered and r.envelope.trace == case_iri]
