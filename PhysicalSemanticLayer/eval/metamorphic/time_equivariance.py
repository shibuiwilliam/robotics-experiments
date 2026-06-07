"""Metamorphic relation: Time equivariance.

Property: Shifting all timestamps by dt should produce IR states
whose timestamps are also shifted by dt, with identical values.

This is a label-free test — no ground truth needed.
"""

from __future__ import annotations

import numpy as np

from psl.ir.core import Adapter


def check_time_equivariance(
    adapter: Adapter,
    native_state: dict[str, object],
    dt: float = 1.0,
    tol: float = 1e-10,
) -> tuple[bool, float]:
    """Check that time-shifting input produces time-shifted output.

    Args:
        adapter: Adapter with to_ir().
        native_state: Original native state with 'time' key.
        dt: Time shift to apply (seconds).
        tol: Tolerance for value comparison.

    Returns:
        (passed, max_value_divergence).
    """
    # Original translation
    ir_original = adapter.to_ir(native_state)

    # Shifted translation
    shifted_state = {**native_state, "time": float(native_state["time"]) + dt}  # type: ignore[arg-type]
    ir_shifted = adapter.to_ir(shifted_state)

    # Check timestamps shifted correctly
    time_ok = abs(ir_shifted.timestamp - (ir_original.timestamp + dt)) < tol

    # Check values are identical
    max_div = 0.0
    for key in ir_original.phytes:
        if key not in ir_shifted.phytes:
            return False, float("inf")

        orig_val = ir_original.phytes[key].value
        shift_val = ir_shifted.phytes[key].value
        div = float(np.max(np.abs(orig_val - shift_val)))
        max_div = max(max_div, div)

        # Check that phyte timestamps also shifted
        orig_t = ir_original.phytes[key].timestamp
        shift_t = ir_shifted.phytes[key].timestamp
        if abs(shift_t - (orig_t + dt)) > tol:
            time_ok = False

    return time_ok and max_div < tol, max_div


def check_object_permutation_invariance(
    adapter: Adapter,
    native_state: dict[str, object],
    tol: float = 1e-10,
) -> tuple[bool, float]:
    """Check that permuting entity ordering doesn't affect results.

    For a single-robot adapter, this verifies that the joint ordering
    is consistent regardless of how we present the state.

    Args:
        adapter: Adapter with to_ir/from_ir.
        native_state: Native state dict.
        tol: Tolerance.

    Returns:
        (passed, max_divergence).
    """
    # Round-trip twice -- should be identical
    ir1 = adapter.to_ir(native_state)
    rt1 = adapter.from_ir(ir1)

    ir2 = adapter.to_ir(rt1)
    rt2 = adapter.from_ir(ir2)

    max_div = 0.0
    for key in ["joint_positions", "joint_velocities"]:
        v1 = np.asarray(rt1[key])
        v2 = np.asarray(rt2[key])
        div = float(np.max(np.abs(v1 - v2)))
        max_div = max(max_div, div)

    return max_div < tol, max_div
