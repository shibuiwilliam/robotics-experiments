"""Deterministic clock for reproducible timestamps in simulation."""

from __future__ import annotations

import time


class DeterministicClock:
    """Clock that produces reproducible timestamps from a seed-based epoch.

    In mock/sim mode, time advances only when explicitly ticked.
    In live mode, wraps wall-clock time.
    """

    def __init__(self, seed: int = 0, start_epoch: float = 1_700_000_000.0) -> None:
        self._seed = seed
        self._epoch = start_epoch
        self._tick_count = 0
        self._dt = 0.01  # 10ms per tick (100Hz default)

    @property
    def now(self) -> float:
        """Current timestamp in seconds since epoch."""
        return self._epoch + self._tick_count * self._dt

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def tick(self, n: int = 1) -> float:
        """Advance clock by n ticks. Returns new timestamp."""
        self._tick_count += n
        return self.now

    def set_dt(self, dt: float) -> None:
        """Set timestep in seconds."""
        self._dt = dt

    def reset(self) -> None:
        """Reset tick counter."""
        self._tick_count = 0


class WallClock:
    """Wrapper around real wall-clock time."""

    @property
    def now(self) -> float:
        return time.time()

    @property
    def tick_count(self) -> int:
        return 0

    def tick(self, n: int = 1) -> float:
        return self.now
