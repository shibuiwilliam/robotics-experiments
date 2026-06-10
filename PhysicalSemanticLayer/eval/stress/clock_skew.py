"""Clock skew stress test — Phase 4.

Injects progressively larger clock skew between two simulated
clock domains and measures causal ordering violations and safety
gate rejections.

The key design: time_uncertainty is a FIXED realistic value (e.g. 5ms
for NTP-synchronized systems), not proportional to skew. This reveals
the gate's actual sensitivity curve: accepted when skew < uncertainty,
rejected when skew >> uncertainty.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3
from psl.phyte.provenance import Provenance, ProvenanceEntry
from psl.safety.gate import PhysicsConsistencyGate

# Default skew magnitudes (seconds) — includes values around the 5ms boundary
DEFAULT_SKEWS: list[float] = [0.0, 0.001, 0.003, 0.005, 0.010, 0.050, 0.100, 0.500, 1.0]

# Default clock uncertainty — 5ms, typical NTP-synchronized system
DEFAULT_CLOCK_UNCERTAINTY: float = 0.005


@dataclass(frozen=True)
class ClockSkewResult:
    """Result of a single clock skew level.

    Fields:
        skew_seconds: Injected skew magnitude (s).
        clock_uncertainty: Declared time uncertainty (s).
        causal_violations: Number of causal ordering violations detected.
        gate_rejections: Number of safety gate rejections.
        n_transitions: Total state transitions tested.
        max_time_error: Maximum absolute time error observed.
        rejection_rate: Fraction of transitions rejected (0.0 to 1.0).
    """

    skew_seconds: float
    clock_uncertainty: float
    causal_violations: int
    gate_rejections: int
    n_transitions: int
    max_time_error: float
    rejection_rate: float


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
    clock_uncertainty: float = DEFAULT_CLOCK_UNCERTAINTY,
) -> ClockSkewResult:
    """Test PSL behaviour under a specific clock skew.

    Uses a FIXED clock_uncertainty (default 5ms) representing realistic
    NTP synchronization quality. Skew below the uncertainty should be
    accepted (normal jitter); skew above should be rejected (genuine
    causal violation).

    Args:
        skew_seconds: Clock offset to inject (s).
        seed: Random seed.
        n_transitions: Number of sequential transitions to test.
        clock_uncertainty: Declared time uncertainty for the remote clock (s).

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
        # Inject skew: remote clock runs BEHIND by skew_seconds
        t_skewed = t_base - skew_seconds

        # Same domain when no skew; cross-domain otherwise
        if skew_seconds == 0.0:
            domain_prev = "sim"
            domain_new = "sim"
            uncertainty = 0.0
        else:
            domain_prev = "sim"
            domain_new = "remote"
            # FIXED uncertainty — not proportional to skew
            uncertainty = clock_uncertainty

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

        for v in result.violations:
            if "causal" in v.lower():
                causal_violations += 1

    rejection_rate = gate_rejections / n_transitions if n_transitions > 0 else 0.0

    return ClockSkewResult(
        skew_seconds=skew_seconds,
        clock_uncertainty=clock_uncertainty,
        causal_violations=causal_violations,
        gate_rejections=gate_rejections,
        n_transitions=n_transitions,
        max_time_error=max_time_error,
        rejection_rate=rejection_rate,
    )


def sweep_clock_skew(
    skew_values: list[float] | None = None,
    seed: int = 42,
    clock_uncertainty: float = DEFAULT_CLOCK_UNCERTAINTY,
) -> list[ClockSkewResult]:
    """Sweep across multiple clock skew magnitudes.

    Args:
        skew_values: List of skew magnitudes to test (s).
            Defaults to DEFAULT_SKEWS.
        seed: Random seed.
        clock_uncertainty: Fixed time uncertainty for the remote clock (s).

    Returns:
        List of ClockSkewResult, one per skew value.
    """
    if skew_values is None:
        skew_values = DEFAULT_SKEWS

    results: list[ClockSkewResult] = []
    for skew in skew_values:
        result = test_clock_skew(skew, seed=seed, clock_uncertainty=clock_uncertainty)
        results.append(result)

    return results
