"""Unit tests for the physics consistency gate."""

from __future__ import annotations

import numpy as np
import pytest

from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3
from psl.safety.gate import JointLimits, PhysicsConsistencyGate


def _jp(idx: int, val: float, t: float = 0.0) -> tuple[str, Phyte]:
    """Helper: create a joint Phyte tuple."""
    return f"joint_{idx}", Phyte(
        semantic_id=f"joint_position_{idx}",
        frame="world",
        pose=identity_se3(),
        timestamp=t,
        unit="rad",
        value=np.array([val]),
        covariance=np.array([[1e-8]]),
    )


@pytest.mark.unit
class TestJointLimits:
    def test_within_limits(self) -> None:
        limits = JointLimits(
            position_lower=np.array([-2.0]),
            position_upper=np.array([2.0]),
            velocity_max=np.array([3.0]),
        )
        gate = PhysicsConsistencyGate(joint_limits=limits)
        state = dict([_jp(0, 0.5)])
        assert gate.check(state).accepted

    def test_at_boundary(self) -> None:
        limits = JointLimits(
            position_lower=np.array([-2.0]),
            position_upper=np.array([2.0]),
            velocity_max=np.array([3.0]),
        )
        gate = PhysicsConsistencyGate(joint_limits=limits)
        state = dict([_jp(0, 2.0)])
        assert gate.check(state).accepted

    def test_beyond_upper(self) -> None:
        limits = JointLimits(
            position_lower=np.array([-2.0]),
            position_upper=np.array([2.0]),
            velocity_max=np.array([3.0]),
        )
        gate = PhysicsConsistencyGate(joint_limits=limits)
        state = dict([_jp(0, 2.1)])
        result = gate.check(state)
        assert not result.accepted

    def test_beyond_lower(self) -> None:
        limits = JointLimits(
            position_lower=np.array([-2.0]),
            position_upper=np.array([2.0]),
            velocity_max=np.array([3.0]),
        )
        gate = PhysicsConsistencyGate(joint_limits=limits)
        state = dict([_jp(0, -2.1)])
        result = gate.check(state)
        assert not result.accepted

    def test_no_limits_set(self) -> None:
        gate = PhysicsConsistencyGate()
        state = dict([_jp(0, 999.0)])
        assert gate.check(state).accepted
