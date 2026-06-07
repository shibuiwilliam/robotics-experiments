"""Baselines for comparison with PSL (PROJECT.md §9).

B0: No semantic layer, hand-written N×N adapters (combinatorial explosion).
B1: Raw data shared blackboard (no semantics).
B2: LLM ad-hoc translation (would call Claude each time — simulated here).

All baselines use the same shared infrastructure (sim, metrics) for fairness.
"""

from __future__ import annotations

import numpy as np

from sim.schema_gen.generator import (
    SchemaTransform,
    apply_schema_transform,
    invert_schema_transform,
)


class BaselineB0:
    """B0: Hand-written N×N adapter (no semantic layer).

    For each pair of robots, a bespoke translation is written.
    This works for 2 robots but scales as O(N^2).

    For identical robots, this is trivially the identity.
    For heterogeneous robots, it must know the specific transform.
    """

    def __init__(self, transform: SchemaTransform) -> None:
        self._transform = transform

    def translate(self, source_state: dict[str, object]) -> dict[str, object]:
        """Direct A→B translation without any semantic layer."""
        return invert_schema_transform(source_state, self._transform)

    @property
    def n_adapters_needed(self) -> int:
        """For N robots, N×N adapters are needed (minus diagonal)."""
        return -1  # Placeholder — actual count depends on N


class BaselineB1:
    """B1: Raw shared blackboard (no semantics).

    Data is shared as-is on a common blackboard. No unit conversion,
    no frame transformation, no uncertainty propagation.
    The receiver must interpret raw values.
    """

    def __init__(self) -> None:
        self._blackboard: dict[str, dict[str, object]] = {}

    def write(self, entity_id: str, state: dict[str, object]) -> None:
        """Write raw state to the blackboard."""
        self._blackboard[entity_id] = dict(state)

    def read(self, entity_id: str) -> dict[str, object] | None:
        """Read raw state from the blackboard (no translation)."""
        return self._blackboard.get(entity_id)


class BaselineB2:
    """B2: LLM ad-hoc translation (simulated).

    In a real system, this would call Claude for every translation.
    Here we simulate it: the LLM "knows" the transform (oracle access)
    and applies it, but adds latency cost and doesn't propagate uncertainty.

    This represents the "just ask the LLM" approach.
    """

    def __init__(self, transform: SchemaTransform) -> None:
        self._transform = transform
        self._call_count = 0

    def translate(self, source_state: dict[str, object]) -> dict[str, object]:
        """Simulate LLM translation — applies the correct transform but at cost."""
        self._call_count += 1
        # The LLM "figures out" the transform (simulated oracle access)
        return invert_schema_transform(source_state, self._transform)

    @property
    def api_calls(self) -> int:
        """Number of simulated API calls."""
        return self._call_count


def run_baseline_comparison(
    base_state: dict[str, object],
    transform: SchemaTransform,
    rng: np.random.Generator,
) -> dict[str, dict[str, float]]:
    """Run all baselines on the same input and return metrics.

    Args:
        base_state: Canonical base state.
        transform: Schema transform applied to create the heterogeneous view.
        rng: Random generator for noise.

    Returns:
        Dict of baseline_name → metrics dict.
    """
    from eval.metrics.contract import round_trip_information_loss
    from eval.metrics.se3 import joint_rmse

    hetero_state = apply_schema_transform(base_state, transform, rng=rng)
    original_jpos = np.asarray(base_state["joint_positions"])

    results: dict[str, dict[str, float]] = {}

    # B0: hand-written adapter (knows the transform)
    b0 = BaselineB0(transform)
    b0_result = b0.translate(hetero_state)
    b0_jpos = np.asarray(b0_result["joint_positions"])
    results["B0"] = {
        "joint_rmse": joint_rmse(b0_jpos, original_jpos),
        "info_loss": round_trip_information_loss(original_jpos, b0_jpos),
    }

    # B1: raw blackboard (no translation)
    b1 = BaselineB1()
    b1.write("source", hetero_state)
    b1_state = b1.read("source")
    assert b1_state is not None
    b1_jpos = np.asarray(b1_state["joint_positions"])
    results["B1"] = {
        "joint_rmse": joint_rmse(b1_jpos, original_jpos),
        "info_loss": round_trip_information_loss(original_jpos, b1_jpos),
    }

    # B2: simulated LLM translation
    b2 = BaselineB2(transform)
    b2_result = b2.translate(hetero_state)
    b2_jpos = np.asarray(b2_result["joint_positions"])
    results["B2"] = {
        "joint_rmse": joint_rmse(b2_jpos, original_jpos),
        "info_loss": round_trip_information_loss(original_jpos, b2_jpos),
        "api_calls": float(b2.api_calls),
    }

    # PSL (via heterogeneous adapter)
    from psl.adapters.robots.panda.adapter import PandaAdapter
    from psl.adapters.robots.panda.heterogeneous import HeterogeneousPandaAdapter
    from psl.ir.translation import translate_r2r

    hetero_adapter = HeterogeneousPandaAdapter(entity_id="h", transform=transform)
    base_adapter = PandaAdapter(entity_id="b")
    psl_result = translate_r2r(hetero_adapter, base_adapter, hetero_state)
    psl_jpos = np.asarray(psl_result["joint_positions"])
    results["PSL"] = {
        "joint_rmse": joint_rmse(psl_jpos, original_jpos),
        "info_loss": round_trip_information_loss(original_jpos, psl_jpos),
    }

    return results
