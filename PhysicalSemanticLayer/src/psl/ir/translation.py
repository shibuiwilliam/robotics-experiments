"""R2R translation pipeline — translate state between robots via the Canonical IR.

All translation goes: Robot A native → IR → Robot B native.
No direct A↔B adapters allowed (N+N invariant).
"""

from __future__ import annotations

from psl.contracts.core import FidelityContract
from psl.ir.core import Adapter


def translate_r2r(
    source_adapter: Adapter,
    target_adapter: Adapter,
    native_state: dict[str, object],
) -> dict[str, object]:
    """Translate from source robot's native state to target robot's native state.

    Path: source_native → IR → target_native.
    This is the N+N path — each adapter only talks to IR.

    Args:
        source_adapter: Adapter for the source robot.
        target_adapter: Adapter for the target robot.
        native_state: Source robot's native state.

    Returns:
        Target robot's native state.
    """
    ir_state = source_adapter.to_ir(native_state)
    return target_adapter.from_ir(ir_state)


def translate_r2r_via_intermediate(
    source_adapter: Adapter,
    intermediate_adapter: Adapter,
    target_adapter: Adapter,
    native_state: dict[str, object],
) -> dict[str, object]:
    """Multi-hop translation: source → IR → intermediate → IR → target.

    Used for compositionality / commutativity testing.

    Args:
        source_adapter: Adapter for the source robot.
        intermediate_adapter: Adapter for the intermediate robot.
        target_adapter: Adapter for the target robot.
        native_state: Source robot's native state.

    Returns:
        Target robot's native state via the intermediate hop.
    """
    ir1 = source_adapter.to_ir(native_state)
    intermediate_native = intermediate_adapter.from_ir(ir1)
    ir2 = intermediate_adapter.to_ir(intermediate_native)
    return target_adapter.from_ir(ir2)


def compose_contracts(
    source_adapter: Adapter,
    target_adapter: Adapter,
) -> FidelityContract:
    """Compose fidelity contracts for a direct R2R path.

    Args:
        source_adapter: Source robot adapter (must have fidelity_contract).
        target_adapter: Target robot adapter (must have fidelity_contract).

    Returns:
        Composed FidelityContract for the full translation path.
    """
    src_contract: FidelityContract = source_adapter.fidelity_contract  # type: ignore[attr-defined]
    tgt_contract: FidelityContract = target_adapter.fidelity_contract  # type: ignore[attr-defined]
    return src_contract.compose(tgt_contract)
