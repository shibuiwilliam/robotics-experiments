"""Event bus — instances speak in JSON-LD semantic envelopes across the sim⇄core boundary.

The envelope's ``@context`` is the generated ontology artifact (CLAUDE.md §5). The bus supports a
deterministic "toxic" mode (drop / delay / partition) to test degradation and bus-partition
behavior (NFR-DEGRADE, FR-BUS) without any real network.
"""

from __future__ import annotations

from core.bus.bus import EventBus, ToxicConfig
from core.bus.envelope import Envelope, make_envelope

__all__ = ["EventBus", "ToxicConfig", "Envelope", "make_envelope"]
