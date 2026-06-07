"""Clock skew stress test — Phase 4.

Injects progressively larger clock skew between two simulated
clock domains and measures causal ordering violations and safety
gate rejections.  This validates that PSL's time_uncertainty and
cross-domain causal checks degrade gracefully.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3
from psl.phyte.provenance import Provenance, ProvenanceEntry
from psl.safety.gate import PhysicsConsistencyGate

# Default skew magnitudes (seconds) to sweep
DEFAULT_SKEWS: list[float] = [0.0, 0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0]


@dataclass(frozen=True)
class ClockSkewResult:
    """Result of a single clock skew level.

    Fields:
        skew_seconds: Injected skew magnitude (s).
        causal_violations: Number of causal ordering violations detected.
        gate_rejections: Number of safety gate rejections.
        n_transitions: Total state transitions tested.
        max_time_error: Maximum absolute time error observed.
    """

    skew_seconds: float
    causal_violations: int
    gate_rejections: int
    n_transitions: int
    max_time_error: float


def _make_phyte_pair(
    name: str,
    value: float,
    t_prev: float,
    t_new: float,
    clock_domain_prev: str,
    clock_domain_new: str,
    time_uncertainty: float,
) -> tuple[Phyte, Phyte]:
    """Create a previous/new Phyte pair for gate checking."""
    base_prov = Provenance(
        chain=[ProvenanceEntry(source="clock_skew_test", operation="inject", timestamp=t_prev)],
        confidence=0.9,
    )
    prev = Phyte(
        semantic_id=f"joint_position:{name}",
        frame="world",
        pose=identity_se3(),
        timestamp=t_prev,
        clock_domain=clock_domain_prev,
        time_uncertainty=0.0,
        unit="rad",
        value=np.array([value]),
        covariance=np.array([[1e-6]]),
        provenance=base_prov,
    )
    new_prov = Provenance(
        chain=[ProvenanceEntry(source="clock_skew_test", operation="inject", timestamp=t_new)],
        confidence=0.9,
    )
    new = Phyte(
        semantic_id=f"joint_position:{name}",
        frame="world",
        pose=identity_se3(),
        timestamp=t_new,
        clock_domain=clock_domain_new,
        time_uncertainty=time_uncertainty,
        unit="rad",
        value=np.array([value + 0.001]),  # small movement
        covariance=np.array([[1e-6]]),
        provenance=new_prov,
    )
    return prev, new


def test_clock_skew(
    skew_seconds: float,
    seed: int = 42,
    n_transitions: int = 20,
) -> ClockSkewResult:
    """Test PSL behaviour under a specific clock skew.

    Creates state transitions where one clock domain is skewed by
    *skew_seconds* relative to the other, then checks for causal
    violations and gate rejections.

    Args:
        skew_seconds: Clock offset to inject (s).
        seed: Random seed.
        n_transitions: Number of sequential transitions to test.

    Returns:
        ClockSkewResult summarising violations at this skew level.
    """
    rng = np.random.default_rng(seed)
    gate = PhysicsConsistencyGate()

    causal_violations = 0
    gate_rejections = 0
    max_time_error = 0.0

    dt = 0.01  # nominal step interval

    for i in range(n_transitions):
        t_base = i * dt
        # Inject skew: the "remote" clock runs BEHIND by skew_seconds,
        # so the new reading appears to come from the past — this triggers
        # causal ordering violations when skew exceeds the uncertainty slack.
        t_skewed = t_base - skew_seconds

        # Use same clock domain when no skew (no cross-domain issues)
        # Use different domains when skew > 0 to exercise cross-domain checks
        if skew_seconds == 0.0:
            domain_prev = "sim"
            domain_new = "sim"
            uncertainty = 0.0
        else:
            domain_prev = "sim"
            domain_new = "remote"
            # Set uncertainty to half the skew — so large skew exceeds the slack
            uncertainty = abs(skew_seconds) * 0.3

        time_error = abs(t_skewed - t_base)
        max_time_error = max(max_time_error, time_error)

        joint_value = rng.uniform(-1.0, 1.0)

        prev_phyte, new_phyte = _make_phyte_pair(
            name="0",
            value=joint_value,
            t_prev=t_base,
            t_new=t_skewed,
            clock_domain_prev=domain_prev,
            clock_domain_new=domain_new,
            time_uncertainty=uncertainty,
        )

        prev_state = {"joint_0": prev_phyte}
        new_state = {"joint_0": new_phyte}

        result = gate.check(new_state, prev_state)

        if not result.accepted:
            gate_rejections += 1

        # Check causal ordering explicitly
        for v in result.violations:
            if "causal" in v.lower():
                causal_violations += 1

    return ClockSkewResult(
        skew_seconds=skew_seconds,
        causal_violations=causal_violations,
        gate_rejections=gate_rejections,
        n_transitions=n_transitions,
        max_time_error=max_time_error,
    )


def sweep_clock_skew(
    skew_values: list[float] | None = None,
    seed: int = 42,
) -> list[ClockSkewResult]:
    """Sweep across multiple clock skew magnitudes.

    Args:
        skew_values: List of skew magnitudes to test (s).
            Defaults to DEFAULT_SKEWS.
        seed: Random seed.

    Returns:
        List of ClockSkewResult, one per skew value.
    """
    if skew_values is None:
        skew_values = DEFAULT_SKEWS

    results: list[ClockSkewResult] = []
    for skew in skew_values:
        result = test_clock_skew(skew, seed=seed)
        results.append(result)

    return results
