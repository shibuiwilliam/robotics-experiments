"""Tests for schema fuzzing stress test."""

from __future__ import annotations

import pytest

from eval.stress.schema_fuzzing import find_breaking_point, fuzz_schema
from psl.adapters.robots.panda.adapter import PandaAdapter
from sim.wrapper import MuJoCoSim


@pytest.mark.unit
class TestSchemaFuzzing:
    def test_fuzz_runs_without_error(self) -> None:
        sim = MuJoCoSim()
        sim.step(100)
        adapter = PandaAdapter()
        results = fuzz_schema(10, seed=42, sim=sim, adapter=adapter)
        assert len(results) == 10

    def test_identity_gives_zero_rmse(self) -> None:
        import numpy as np

        from psl.adapters.robots.panda.heterogeneous import HeterogeneousPandaAdapter
        from psl.ir.translation import translate_r2r
        from sim.schema_gen.generator import SchemaTransform

        sim = MuJoCoSim()
        sim.step(100)
        state: dict[str, object] = {
            "joint_positions": sim.get_joint_positions(),
            "joint_velocities": sim.get_joint_velocities(),
            "ee_position": sim.get_ee_pose()[0],
            "ee_quaternion": sim.get_ee_pose()[1],
            "time": sim.time,
        }
        transform = SchemaTransform()  # identity
        ha = HeterogeneousPandaAdapter(entity_id="h", transform=transform)
        ba = PandaAdapter(entity_id="b")
        result = translate_r2r(ha, ba, state)
        rmse = float(
            np.sqrt(
                np.mean(
                    (np.asarray(result["joint_positions"]) - np.asarray(state["joint_positions"]))
                    ** 2
                )
            )
        )
        assert rmse < 1e-10

    def test_noise_gives_nonzero_rmse(self) -> None:
        sim = MuJoCoSim()
        sim.step(100)
        adapter = PandaAdapter()
        # Use high noise to ensure non-zero RMSE
        results = fuzz_schema(5, seed=99, sim=sim, adapter=adapter)
        noisy = [r for r in results if r.sensor_noise_std > 0.01]
        if noisy:
            assert any(r.joint_rmse > 0 for r in noisy)

    def test_find_breaking_point_returns_valid(self) -> None:
        sim = MuJoCoSim()
        sim.step(100)
        adapter = PandaAdapter()
        results = fuzz_schema(15, seed=42, sim=sim, adapter=adapter)
        bp = find_breaking_point(results)
        assert "noise_breaking_std" in bp
        assert "max_rmse" in bp
        assert bp["max_rmse"] >= 0
