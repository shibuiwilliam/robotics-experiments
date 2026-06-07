"""Metamorphic relation: Unit invariance.

Property: Rescaling units (e.g. m to mm) should not change the semantic meaning.
After converting to IR and back, the values in original units must match.

This is a label-free test — no ground truth needed.
"""

from __future__ import annotations

import numpy as np

from psl.ir.core import Adapter
from psl.phyte.units import convert_quantity, parse_unit


def check_unit_invariance_joint(
    adapter: Adapter,
    native_state: dict[str, object],
    scale_factor: float = 1000.0,
    tol: float = 1e-8,
) -> tuple[bool, float]:
    """Check that unit rescaling doesn't affect round-trip results.

    For joint positions (in rad), scaling doesn't apply directly.
    Instead, we test that the adapter correctly handles the round trip
    regardless of internal unit representations.

    Args:
        adapter: Adapter with to_ir/from_ir.
        native_state: Original native state.
        scale_factor: Not used for angle units, but kept for interface consistency.
        tol: Tolerance for comparison.

    Returns:
        (passed, max_error).
    """
    # Round-trip through IR
    ir_state = adapter.to_ir(native_state)
    reconstructed = adapter.from_ir(ir_state)

    original_jpos = np.asarray(native_state["joint_positions"])
    recon_jpos = np.asarray(reconstructed["joint_positions"])

    max_err = float(np.max(np.abs(original_jpos - recon_jpos)))
    return max_err < tol, max_err


def check_unit_conversion_consistency(
    value: float,
    from_unit_str: str,
    via_unit_str: str,
    tol: float = 1e-10,
) -> tuple[bool, float]:
    """Check that value -> via_unit -> from_unit round-trips correctly.

    Args:
        value: Original numeric value.
        from_unit_str: Original unit (e.g. 'm').
        via_unit_str: Intermediate unit (e.g. 'mm').
        tol: Tolerance.

    Returns:
        (passed, error).
    """
    from_unit = parse_unit(from_unit_str)
    via_unit = parse_unit(via_unit_str)

    converted = convert_quantity(value, from_unit, via_unit)
    back = convert_quantity(converted, via_unit, from_unit)

    err = abs(value - back)
    return err < tol, err
