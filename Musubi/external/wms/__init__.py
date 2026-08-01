"""WMS mock — the business ledger (book of record) with intentional reality divergence.

The WMS holds the *book* state (where inventory is recorded to be, its lot, its owner). The
Invisible Hand perturbs *reality* (the sim), so book ≠ reality — the divergence Musubi must detect,
mediate, and explain. In-process (D-0007), not networked.
"""

from __future__ import annotations

from external.wms.ledger import LedgerRecord, WMSLedger

__all__ = ["WMSLedger", "LedgerRecord"]
