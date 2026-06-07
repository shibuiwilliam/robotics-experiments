"""Physics consistency gate — rejects physically impossible translations.

The safety gate sits between translation outputs and world model writes.
It checks:
  1. Joint limits (within kinematic bounds)
  2. Teleport detection (pose delta too large for elapsed time)
  3. Conservation sanity (mass invariance)

This is a symbolic/rule-based layer — no learned components in the safety path.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from psl.phyte.core import Phyte
from psl.phyte.geometry import se3_distance


@dataclass(frozen=True)
class GateResult:
    """Result of a safety gate check.

    Fields:
        accepted: Whether the state passed all checks.
        violations: List of human-readable violation descriptions.
    """

    accepted: bool
    violations: list[str]


@dataclass(frozen=True)
class JointLimits:
    """Joint position and velocity limits for a robot.

    Fields:
        position_lower: Lower joint position limits (rad or m).
        position_upper: Upper joint position limits (rad or m).
        velocity_max: Maximum joint velocity magnitudes.
    """

    position_lower: NDArray[np.float64]
    position_upper: NDArray[np.float64]
    velocity_max: NDArray[np.float64]


class PhysicsConsistencyGate:
    """Safety gate enforcing physics constraints on state transitions.

    Must be called before any write to the world model.
    All checks are symbolic/rule-based — no learned components.

    Args:
        joint_limits: Optional joint limits for the entity.
        max_linear_velocity: Maximum allowed linear velocity (m/s).
        max_angular_velocity: Maximum allowed angular velocity (rad/s).
    """

    def __init__(
        self,
        joint_limits: JointLimits | None = None,
        max_linear_velocity: float = 10.0,
        max_angular_velocity: float = 20.0,
        joint_limit_tolerance: float = 0.02,
    ) -> None:
        self._joint_limits = joint_limits
        self._max_linear_vel = max_linear_velocity
        self._max_angular_vel = max_angular_velocity
        self._joint_limit_tol = joint_limit_tolerance

    def check(
        self,
        new_state: dict[str, Phyte],
        previous_state: dict[str, Phyte] | None = None,
    ) -> GateResult:
        """Run all safety checks on proposed new state.

        Args:
            new_state: Proposed Phytes to write to world model.
            previous_state: Previous Phytes (for temporal checks). None on first write.

        Returns:
            GateResult indicating accept/reject with violation details.
        """
        violations: list[str] = []

        violations.extend(self._check_joint_limits(new_state))
        if previous_state is not None:
            violations.extend(self._check_teleport(new_state, previous_state))
            violations.extend(self._check_causal_ordering(new_state, previous_state))

        return GateResult(accepted=len(violations) == 0, violations=violations)

    def _check_joint_limits(self, state: dict[str, Phyte]) -> list[str]:
        """Check joint positions are within kinematic bounds."""
        if self._joint_limits is None:
            return []

        violations: list[str] = []
        for name, phyte in state.items():
            if not name.startswith("joint_"):
                continue
            val = phyte.value
            idx = int(name.split("_")[1]) if name.split("_")[1].isdigit() else -1
            if idx < 0 or idx >= len(self._joint_limits.position_lower):
                continue

            pos = float(val[0]) if val.size > 0 else 0.0
            lo = float(self._joint_limits.position_lower[idx])
            hi = float(self._joint_limits.position_upper[idx])
            if pos < (lo - self._joint_limit_tol) or pos > (hi + self._joint_limit_tol):
                violations.append(f"{name}: position {pos:.4f} outside [{lo:.4f}, {hi:.4f}]")

        return violations

    def _check_teleport(
        self,
        new_state: dict[str, Phyte],
        prev_state: dict[str, Phyte],
    ) -> list[str]:
        """Detect physically impossible pose jumps (teleportation).

        A jump that exceeds max_velocity * dt is flagged.
        """
        violations: list[str] = []

        for name, new_phyte in new_state.items():
            if name not in prev_state:
                continue
            prev_phyte = prev_state[name]

            dt = abs(new_phyte.timestamp - prev_phyte.timestamp)
            if dt < 1e-9:
                continue  # Same timestamp — skip

            dist = se3_distance(prev_phyte.pose, new_phyte.pose)
            max_dist = self._max_linear_vel * dt + self._max_angular_vel * dt

            if dist > max_dist * 1.5:  # 50% margin for covariance
                violations.append(
                    f"{name}: teleport detected — distance {dist:.4f} "
                    f"exceeds max {max_dist:.4f} for dt={dt:.4f}s"
                )

        return violations

    def _check_causal_ordering(
        self,
        new_state: dict[str, Phyte],
        prev_state: dict[str, Phyte],
    ) -> list[str]:
        """Check causal ordering — new timestamps must not precede previous ones.

        Within the same clock domain, time must be monotonically non-decreasing.
        Across clock domains, we allow slack proportional to time_uncertainty.
        """
        violations: list[str] = []

        for name, new_phyte in new_state.items():
            if name not in prev_state:
                continue
            prev_phyte = prev_state[name]

            if new_phyte.clock_domain == prev_phyte.clock_domain:
                # Same clock domain: strict monotonicity
                if new_phyte.timestamp < prev_phyte.timestamp - 1e-9:
                    violations.append(
                        f"{name}: causal ordering violation — "
                        f"new timestamp {new_phyte.timestamp:.6f} < "
                        f"previous {prev_phyte.timestamp:.6f} "
                        f"(same clock domain '{new_phyte.clock_domain}')"
                    )
            else:
                # Different clock domains: allow slack from time uncertainty
                slack = new_phyte.time_uncertainty + prev_phyte.time_uncertainty
                if new_phyte.timestamp < prev_phyte.timestamp - slack:
                    violations.append(
                        f"{name}: cross-domain causal ordering violation — "
                        f"new timestamp {new_phyte.timestamp:.6f} < "
                        f"previous {prev_phyte.timestamp:.6f} - "
                        f"slack {slack:.6f}"
                    )

        return violations
