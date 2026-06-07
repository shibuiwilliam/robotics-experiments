"""Metamorphic relation: Compositionality (commutativity).

Property: A→IR→B should approximately equal A→IR→C→IR→B.
The divergence between direct and multi-hop paths measures
commutativity violation.

This is the core test for N+N translation quality.
"""

from __future__ import annotations

import numpy as np

from psl.ir.core import Adapter
from psl.ir.translation import translate_r2r, translate_r2r_via_intermediate


def check_compositionality(
    source_adapter: Adapter,
    intermediate_adapter: Adapter,
    target_adapter: Adapter,
    native_state: dict[str, object],
    tol: float = 1e-6,
) -> tuple[bool, float]:
    """Check that direct and multi-hop translations approximately agree.

    Direct:    A → IR → B
    Multi-hop: A → IR → C → IR → B

    Args:
        source_adapter: Source robot adapter (A).
        intermediate_adapter: Intermediate robot adapter (C).
        target_adapter: Target robot adapter (B).
        native_state: Source robot's native state.
        tol: Maximum allowed divergence.

    Returns:
        (passed, divergence) -- passed is True if divergence < tol.
    """
    direct_result = translate_r2r(source_adapter, target_adapter, native_state)
    multihop_result = translate_r2r_via_intermediate(
        source_adapter, intermediate_adapter, target_adapter, native_state
    )

    # Compare joint positions and velocities
    max_div = 0.0
    for key in ["joint_positions", "joint_velocities"]:
        v_direct = np.asarray(direct_result[key])
        v_multi = np.asarray(multihop_result[key])
        div = float(np.max(np.abs(v_direct - v_multi)))
        max_div = max(max_div, div)

    return max_div < tol, max_div


def commutativity_divergence(
    adapter_a: Adapter,
    adapter_b: Adapter,
    native_state_a: dict[str, object],
) -> float:
    """Measure commutativity divergence for A → B path.

    Computes: ‖from_ir_B(to_ir_A(x)) - from_ir_B(to_ir_B(from_ir_A(to_ir_A(x))))‖

    Args:
        adapter_a: Source adapter.
        adapter_b: Target adapter.
        native_state_a: Source state.

    Returns:
        Scalar divergence (0 = perfectly commutative).
    """
    direct = translate_r2r(adapter_a, adapter_b, native_state_a)
    multihop = translate_r2r_via_intermediate(adapter_a, adapter_b, adapter_b, native_state_a)

    divs = []
    for key in ["joint_positions", "joint_velocities"]:
        v1 = np.asarray(direct[key])
        v2 = np.asarray(multihop[key])
        divs.append(float(np.linalg.norm(v1 - v2)))
    return max(divs)
