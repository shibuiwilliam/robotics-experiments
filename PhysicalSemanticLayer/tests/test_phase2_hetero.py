"""Phase 2 tests: Heterogeneous robots and dose-response.

Tests schema generator, heterogeneous adapter, and the dose-response
sweep runner.
"""

from __future__ import annotations

import numpy as np
import pytest

from eval.metamorphic.compositionality import check_compositionality
from eval.runner.sweep import run_dose_response_sweep
from psl.adapters.robots.panda.adapter import PandaAdapter
from psl.adapters.robots.panda.heterogeneous import HeterogeneousPandaAdapter
from psl.ir.translation import translate_r2r
from sim.schema_gen.generator import (
    SchemaTransform,
    apply_schema_transform,
    invert_schema_transform,
)
from sim.wrapper import MuJoCoSim

SEED = 42


@pytest.fixture
def base_state() -> dict[str, object]:
    sim = MuJoCoSim(seed=SEED)
    sim.step(200)
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


@pytest.mark.unit
class TestSchemaGenerator:
    def test_identity_transform(self, base_state: dict[str, object]) -> None:
        t = SchemaTransform()  # identity
        transformed = apply_schema_transform(base_state, t)
        np.testing.assert_allclose(
            np.asarray(transformed["joint_positions"]),
            np.asarray(base_state["joint_positions"]),
            atol=1e-15,
        )

    def test_unit_scaling(self, base_state: dict[str, object]) -> None:
        t = SchemaTransform(unit_scale=1000.0)
        transformed = apply_schema_transform(base_state, t)
        np.testing.assert_allclose(
            np.asarray(transformed["joint_positions"]),
            np.asarray(base_state["joint_positions"]) * 1000.0,
            atol=1e-10,
        )

    def test_unit_scaling_invertible(self, base_state: dict[str, object]) -> None:
        t = SchemaTransform(unit_scale=1000.0)
        transformed = apply_schema_transform(base_state, t)
        recovered = invert_schema_transform(transformed, t)
        np.testing.assert_allclose(
            np.asarray(recovered["joint_positions"]),
            np.asarray(base_state["joint_positions"]),
            atol=1e-10,
        )

    def test_frame_rotation_invertible(self, base_state: dict[str, object]) -> None:
        t = SchemaTransform(frame_rotation_z_rad=np.pi / 4)
        transformed = apply_schema_transform(base_state, t)
        recovered = invert_schema_transform(transformed, t)
        np.testing.assert_allclose(
            np.asarray(recovered["ee_position"]),
            np.asarray(base_state["ee_position"]),
            atol=1e-10,
        )

    def test_noise_not_invertible(self, base_state: dict[str, object]) -> None:
        rng = np.random.default_rng(SEED)
        t = SchemaTransform(sensor_noise_std=0.1)
        transformed = apply_schema_transform(base_state, t, rng=rng)
        recovered = invert_schema_transform(transformed, t)
        # Noise is NOT inverted — recovered should differ from base
        diff = np.max(
            np.abs(
                np.asarray(recovered["joint_positions"])
                - np.asarray(base_state["joint_positions"])
            )
        )
        assert diff > 0.001, "Noise should not be inverted"


@pytest.mark.oracle
class TestHeterogeneousAdapter:
    def test_noiseless_round_trip(self, base_state: dict[str, object]) -> None:
        """Noiseless heterogeneous adapter should round-trip exactly."""
        t = SchemaTransform(unit_scale=1000.0, frame_rotation_z_rad=np.pi / 3)
        hetero = HeterogeneousPandaAdapter(entity_id="h", transform=t)
        base = PandaAdapter(entity_id="b")

        hetero_state = apply_schema_transform(base_state, t)
        translated = translate_r2r(hetero, base, hetero_state)

        np.testing.assert_allclose(
            np.asarray(translated["joint_positions"]),
            np.asarray(base_state["joint_positions"]),
            atol=1e-10,
        )

    def test_noisy_round_trip_degrades(self, base_state: dict[str, object]) -> None:
        """Noisy transform should degrade round-trip quality."""
        rng = np.random.default_rng(SEED)
        t = SchemaTransform(sensor_noise_std=0.1)
        hetero = HeterogeneousPandaAdapter(entity_id="h", transform=t)
        base = PandaAdapter(entity_id="b")

        hetero_state = apply_schema_transform(base_state, t, rng=rng)
        translated = translate_r2r(hetero, base, hetero_state)

        err = np.max(
            np.abs(
                np.asarray(translated["joint_positions"])
                - np.asarray(base_state["joint_positions"])
            )
        )
        assert err > 0.001, "Noisy transform should cause measurable error"

    def test_fidelity_contract_reflects_noise(self) -> None:
        t_clean = SchemaTransform()
        t_noisy = SchemaTransform(sensor_noise_std=0.1)
        clean = HeterogeneousPandaAdapter(transform=t_clean)
        noisy = HeterogeneousPandaAdapter(transform=t_noisy)
        assert (
            noisy.fidelity_contract.uncertainty_delta > clean.fidelity_contract.uncertainty_delta
        )


@pytest.mark.metamorphic
class TestHeterogeneousCompositionality:
    def test_noiseless_compositionality(self, base_state: dict[str, object]) -> None:
        """Noiseless heterogeneous adapter should have low commutativity divergence."""
        t = SchemaTransform(unit_scale=1000.0, frame_rotation_z_rad=np.pi / 6)
        base = PandaAdapter(entity_id="b")
        hetero = HeterogeneousPandaAdapter(entity_id="h", transform=t)

        hetero_state = apply_schema_transform(base_state, t)
        passed, div = check_compositionality(hetero, base, hetero, hetero_state, tol=1e-8)
        assert passed, f"Compositionality violated: divergence={div:.2e}"


@pytest.mark.oracle
class TestDoseResponseSweep:
    def test_sweep_runs(self) -> None:
        """Sweep should complete and produce results for each dose."""
        report = run_dose_response_sweep(seed=SEED, n_steps=100)
        assert len(report.results) == 6  # 6 doses in HETEROGENEITY_DOSES
        assert report.wall_time_s > 0

    def test_sweep_dose_response_trend(self) -> None:
        """Higher doses should generally produce higher RMSE (dose-response)."""
        report = run_dose_response_sweep(seed=SEED, n_steps=100)
        # Dose 0 (identity) should have ~0 RMSE
        assert report.results[0].joint_pos_rmse < 1e-10
        # Dose with noise should have higher RMSE
        noisy_result = report.results[3]  # sensor_noise_std=0.01
        assert noisy_result.joint_pos_rmse > 1e-4
