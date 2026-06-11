"""Latency decomposition instrumentation."""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field

from mws.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class LatencyRecord:
    """A single latency measurement."""

    component: str
    duration_ms: float


@dataclass
class LatencyTracker:
    """Tracks latency decomposition across pipeline components."""

    records: list[LatencyRecord] = field(default_factory=list)

    @contextmanager
    def track(self, component: str) -> Generator[None, None, None]:
        """Context manager to measure component latency."""
        start = time.perf_counter()
        yield
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.records.append(LatencyRecord(component=component, duration_ms=elapsed_ms))

    def summary(self) -> dict[str, float]:
        """Get latency summary by component."""
        by_component: dict[str, list[float]] = {}
        for r in self.records:
            by_component.setdefault(r.component, []).append(r.duration_ms)

        result = {}
        for comp, times in by_component.items():
            sorted_times = sorted(times)
            n = len(sorted_times)
            result[f"{comp}_p50_ms"] = sorted_times[n // 2]
            result[f"{comp}_p95_ms"] = sorted_times[int(n * 0.95)]
            result[f"{comp}_count"] = float(n)
        return result
