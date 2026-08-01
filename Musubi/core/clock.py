"""Deterministic time source (NFR-DETERM, CLAUDE.md §0-5).

No production path may call ``time.time()`` / ``datetime.now()``. All time comes from a
:class:`SimClock`, advanced explicitly by the scenario/sim. This makes runs reproducible and
localizes non-determinism to the LLM (behind the VCR).

Bitemporality: the same clock supplies *valid time* (when a fact holds in the world) and, when a
store records a Claim, *transaction time* (when it was written). Both are sim seconds since the
run epoch, so a replay reproduces them bit-for-bit.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimClock:
    """Monotonic simulation clock in seconds since the run epoch (t=0.0)."""

    _t: float = 0.0
    _transactions: int = field(default=0, repr=False)

    def now(self) -> float:
        """Current sim time (seconds)."""
        return self._t

    def advance(self, dt: float) -> float:
        """Advance the clock by ``dt`` seconds (must be >= 0). Returns the new time."""
        if dt < 0:
            raise ValueError(f"cannot advance clock by negative dt={dt}")
        self._t += dt
        return self._t

    def set(self, t: float) -> None:
        """Set absolute sim time (monotonic: must not move backwards)."""
        if t < self._t:
            raise ValueError(f"clock is monotonic; cannot set t={t} < current {self._t}")
        self._t = t

    def tick_transaction(self) -> float:
        """Return the current time and count a transaction (for transaction-time stamping)."""
        self._transactions += 1
        return self._t

    @property
    def transactions(self) -> int:
        return self._transactions
