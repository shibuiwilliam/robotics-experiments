"""Belief mediation — adjudicate conflicting Claims (FR-MED, E2).

Winner = argmax over active Claims of ``authority × decayed_confidence × method_rank``. The loser
is never deleted; on ``commit`` it is superseded (append-only) so ``active`` queries return only
the winner. Read-time :func:`Mediator.resolve` picks the current belief without mutating the store.
"""

from __future__ import annotations

from core.mediator.mediator import MediationOutcome, Mediator

__all__ = ["Mediator", "MediationOutcome"]
