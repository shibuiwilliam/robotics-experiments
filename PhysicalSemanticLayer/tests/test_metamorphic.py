"""Metamorphic / property-based tests using hypothesis.

Tests invariance properties that hold regardless of ground truth:
  - Frame equivariance: T(g·x) == g·T(x)
  - Unit invariance: unit round-trips are exact
  - Time equivariance: time shift in → time shift out
  - Object permutation invariance: double round-trip is idempotent
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from eval.metamorphic.frame_equivariance import check_frame_equivariance
from eval.metamorphic.time_equivariance import (
    check_object_permutation_invariance,
    check_time_equivariance,
)
from eval.metamorphic.unit_invariance import (
    check_unit_conversion_consistency,
    check_unit_invariance_joint,
)
from psl.adapters.robots.panda.adapter import PandaAdapter
from psl.phyte.geometry import random_se3
from sim.wrapper import MuJoCoSim

SEED = 42


def _make_native_state() -> dict[str, object]:
    """Create a native state dict from a stepped sim (no fixture needed)."""
    sim = MuJoCoSim(seed=SEED)
    sim.step(100)
    jpos = sim.get_joint_positions()
    jvel = sim.get_joint_velocities()
    ee_pos, ee_quat = sim.get_ee_pose()
    return {
        "joint_positions": jpos,
        "joint_velocities": jvel,
        "ee_position": ee_pos,
        "ee_quaternion": ee_quat,
        "time": sim.time,
    }


@pytest.mark.metamorphic
class TestFrameEquivariance:
    """T(g · x) == g · T(x) for random SE(3) transforms g."""

    @given(seed=st.integers(min_value=0, max_value=10000))
    @settings(max_examples=20, derandomize=True)
    def test_frame_equivariance(self, seed: int) -> None:
        native_state = _make_native_state()
        adapter = PandaAdapter(entity_id="panda_test")
        rng = np.random.default_rng(seed)
        g = random_se3(rng)
        passed, divergence = check_frame_equivariance(native_state, adapter, g, tol=1e-6)
        assert passed, f"Frame equivariance violated: divergence={divergence:.2e}"


@pytest.mark.metamorphic
class TestUnitInvariance:
    """Unit conversions must round-trip exactly."""

    def test_unit_invariance_joint_round_trip(
        self,
        native_state: dict[str, object],
        adapter: PandaAdapter,
    ) -> None:
        passed, max_err = check_unit_invariance_joint(adapter, native_state)
        assert passed, f"Unit invariance violated: max_err={max_err:.2e}"

    @given(
        value=st.floats(min_value=0.001, max_value=1000.0, allow_nan=False),
    )
    @settings(max_examples=50, derandomize=True)
    def test_unit_conversion_m_mm(self, value: float) -> None:
        passed, err = check_unit_conversion_consistency(value, "m", "mm")
        assert passed, f"m↔mm conversion error: {err:.2e}"

    @given(
        value=st.floats(min_value=0.001, max_value=1000.0, allow_nan=False),
    )
    @settings(max_examples=50, derandomize=True)
    def test_unit_conversion_rad_deg(self, value: float) -> None:
        passed, err = check_unit_conversion_consistency(value, "rad", "deg")
        assert passed, f"rad↔deg conversion error: {err:.2e}"


@pytest.mark.metamorphic
class TestTimeEquivariance:
    """Time-shifting input must time-shift output without changing values."""

    @given(dt=st.floats(min_value=-100.0, max_value=100.0, allow_nan=False))
    @settings(max_examples=30, derandomize=True)
    def test_time_shift(self, dt: float) -> None:
        native_state = _make_native_state()
        adapter = PandaAdapter(entity_id="panda_test")
        passed, div = check_time_equivariance(adapter, native_state, dt=dt)
        assert passed, f"Time equivariance violated: divergence={div:.2e}"


@pytest.mark.metamorphic
class TestObjectPermutationInvariance:
    """Double round-trip must be idempotent."""

    def test_double_round_trip(
        self,
        native_state: dict[str, object],
        adapter: PandaAdapter,
    ) -> None:
        passed, div = check_object_permutation_invariance(adapter, native_state)
        assert passed, f"Permutation invariance violated: divergence={div:.2e}"
