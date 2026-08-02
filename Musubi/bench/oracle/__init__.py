"""Oracle engine — the machine-scoring contract (SCENARIOS.md §3).

An oracle has four quadrants: ``success`` (per-repeat bool), ``must`` (invariant across all repeats
and arms ≥A3), ``acceptable_world`` (terminal-world SHACL), and ``endpoints`` (repeat-aggregated
thresholds). Scoring reads only the four data sources in :class:`RunContext`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bench.oracle.context import RunContext
from bench.oracle.evaluator import (
    OracleError,
    evaluate,
    evaluate_endpoint,
    evaluate_must,
)

__all__ = [
    "RunContext",
    "OracleError",
    "OracleSpec",
    "OracleResult",
    "score_oracle",
    "evaluate",
    "evaluate_must",
    "evaluate_endpoint",
]


@dataclass
class OracleSpec:
    success: str | None = None
    must: list[str] = field(default_factory=list)
    acceptable_world: str | None = None
    endpoints: list[str] = field(default_factory=list)

    @staticmethod
    def from_dict(data: dict[str, Any] | list[str] | None) -> OracleSpec:
        if data is None:
            return OracleSpec()
        if isinstance(data, list):  # legacy: a bare list of must-style checks
            return OracleSpec(must=[str(x) for x in data])
        return OracleSpec(
            success=data.get("success"),
            must=list(data.get("must", [])),
            acceptable_world=data.get("acceptable_world"),
            endpoints=list(data.get("endpoints", [])),
        )


@dataclass
class OracleResult:
    passed: bool
    success_rate: float
    must_ok: bool
    acceptable_ok: bool
    endpoints: dict[str, bool] = field(default_factory=dict)
    must_failures: list[str] = field(default_factory=list)


def score_oracle(spec: OracleSpec, contexts: list[RunContext]) -> OracleResult:
    """Score an oracle across a scenario's repeat contexts (all same arm)."""
    if not contexts:
        return OracleResult(False, 0.0, False, False)

    successes = [bool(evaluate(spec.success, c)) for c in contexts] if spec.success else [True]
    success_rate = sum(successes) / len(successes)

    must_failures: list[str] = []
    for c in contexts:
        for m in spec.must:
            if not evaluate_must(m, c):
                must_failures.append(m)
    must_ok = not must_failures

    acceptable_ok = (
        all(bool(evaluate(spec.acceptable_world, c)) for c in contexts)
        if spec.acceptable_world
        else True
    )

    endpoints = {e: evaluate_endpoint(e, contexts) for e in spec.endpoints}

    if spec.endpoints:
        passed = must_ok and acceptable_ok and all(endpoints.values())
    else:
        passed = must_ok and acceptable_ok and success_rate >= 1.0

    return OracleResult(
        passed=passed,
        success_rate=success_rate,
        must_ok=must_ok,
        acceptable_ok=acceptable_ok,
        endpoints=endpoints,
        must_failures=sorted(set(must_failures)),
    )
