"""Phase 1 tests: R2R translation via shared world model.

Tests two identical Panda robots sharing a world model,
R2R translation through the IR, and compositionality.
"""

from __future__ import annotations

import numpy as np
import pytest

from eval.metamorphic.compositionality import check_compositionality, commutativity_divergence
from psl.adapters.robots.panda.adapter import PandaAdapter
from psl.ir.translation import translate_r2r
from psl.safety.gate import JointLimits, PhysicsConsistencyGate
from psl.world_model.core import WorldModel
from sim.dual_wrapper import MultiRobotSim

SEED = 42


@pytest.fixture
def dual_sim() -> MultiRobotSim:
    return MultiRobotSim(seed=SEED)


@pytest.fixture
def adapter_a() -> PandaAdapter:
    return PandaAdapter(entity_id="panda_a")


@pytest.fixture
def adapter_b() -> PandaAdapter:
    return PandaAdapter(entity_id="panda_b")


@pytest.fixture
def world_model(dual_sim: MultiRobotSim) -> WorldModel:
    lower, upper = dual_sim.get_joint_limits()
    limits = JointLimits(
        position_lower=lower,
        position_upper=upper,
        velocity_max=np.full(7, 2.61),
    )
    gate_a = PhysicsConsistencyGate(joint_limits=limits)
    gate_b = PhysicsConsistencyGate(joint_limits=limits)
    wm = WorldModel(safety_gates={"panda_a": gate_a, "panda_b": gate_b})
    wm.register_entity("world")
    wm.register_entity("panda_a", parent_id="world")
    wm.register_entity("panda_b", parent_id="world")
    wm.register_entity("blue_gear", parent_id="world")
    return wm


@pytest.mark.oracle
class TestDualRobotSim:
    """Test the dual-robot sim loads and reads sensors."""

    def test_dual_sim_loads(self, dual_sim: MultiRobotSim) -> None:
        assert dual_sim.robot_prefixes == ["a_", "b_"]

    def test_step_and_read(self, dual_sim: MultiRobotSim) -> None:
        dual_sim.step(100)
        state_a = dual_sim.get_robot_state("a_")
        state_b = dual_sim.get_robot_state("b_")
        assert np.asarray(state_a["joint_positions"]).shape == (7,)
        assert np.asarray(state_b["joint_positions"]).shape == (7,)


@pytest.mark.oracle
class TestR2RTranslation:
    """R2R translation: Panda A → IR → Panda B."""

    def test_r2r_identity_translation(
        self,
        dual_sim: MultiRobotSim,
        adapter_a: PandaAdapter,
        adapter_b: PandaAdapter,
    ) -> None:
        """For identical robots, R2R should be near-lossless."""
        dual_sim.step(100)
        state_a = dual_sim.get_robot_state("a_")

        translated = translate_r2r(adapter_a, adapter_b, state_a)
        original_jpos = np.asarray(state_a["joint_positions"])
        translated_jpos = np.asarray(translated["joint_positions"])
        np.testing.assert_allclose(translated_jpos, original_jpos, atol=1e-12)

    def test_r2r_preserves_timestamp(
        self,
        dual_sim: MultiRobotSim,
        adapter_a: PandaAdapter,
        adapter_b: PandaAdapter,
    ) -> None:
        dual_sim.step(50)
        state_a = dual_sim.get_robot_state("a_")
        translated = translate_r2r(adapter_a, adapter_b, state_a)
        assert translated["time"] == state_a["time"]


@pytest.mark.oracle
class TestSharedWorldModel:
    """Both robots write/read through the shared world model."""

    def test_both_robots_write(
        self,
        dual_sim: MultiRobotSim,
        adapter_a: PandaAdapter,
        adapter_b: PandaAdapter,
        world_model: WorldModel,
    ) -> None:
        dual_sim.step(100)
        state_a = dual_sim.get_robot_state("a_")
        state_b = dual_sim.get_robot_state("b_")

        ir_a = adapter_a.to_ir(state_a)
        ir_b = adapter_b.to_ir(state_b)

        result_a = world_model.write("panda_a", ir_a.phytes)
        result_b = world_model.write("panda_b", ir_b.phytes)
        assert result_a.accepted
        assert result_b.accepted

        read_a = world_model.read("panda_a")
        read_b = world_model.read("panda_b")
        assert len(read_a) > 0
        assert len(read_b) > 0

    def test_r2r_via_world_model(
        self,
        dual_sim: MultiRobotSim,
        adapter_a: PandaAdapter,
        adapter_b: PandaAdapter,
        world_model: WorldModel,
    ) -> None:
        """Full R2R path through world model: A writes, B reads and translates."""
        dual_sim.step(100)
        state_a = dual_sim.get_robot_state("a_")

        # A writes to world model
        ir_a = adapter_a.to_ir(state_a)
        world_model.write("panda_a", ir_a.phytes)

        # B reads A's state from world model and translates
        wm_state = world_model.read("panda_a")
        # Reconstruct IRState from world model Phytes
        from psl.ir.core import IRState

        ir_from_wm = IRState(
            entity_id="panda_a",
            phytes=wm_state,
            timestamp=ir_a.timestamp,
            clock_domain="sim",
        )
        b_native = adapter_b.from_ir(ir_from_wm)

        original_jpos = np.asarray(state_a["joint_positions"])
        translated_jpos = np.asarray(b_native["joint_positions"])
        np.testing.assert_allclose(translated_jpos, original_jpos, atol=1e-12)


@pytest.mark.metamorphic
class TestCompositionality:
    """MR5: Direct vs multi-hop translation commutativity."""

    def test_compositionality_identical_robots(
        self,
        dual_sim: MultiRobotSim,
        adapter_a: PandaAdapter,
        adapter_b: PandaAdapter,
    ) -> None:
        """For identical robots, compositionality should hold perfectly."""
        dual_sim.step(100)
        state_a = dual_sim.get_robot_state("a_")
        passed, div = check_compositionality(adapter_a, adapter_b, adapter_a, state_a, tol=1e-10)
        assert passed, f"Compositionality violated: divergence={div:.2e}"

    def test_commutativity_divergence_is_zero(
        self,
        dual_sim: MultiRobotSim,
        adapter_a: PandaAdapter,
        adapter_b: PandaAdapter,
    ) -> None:
        """For identical robots, commutativity divergence should be ~0."""
        dual_sim.step(100)
        state_a = dual_sim.get_robot_state("a_")
        div = commutativity_divergence(adapter_a, adapter_b, state_a)
        assert div < 1e-10, f"Commutativity divergence: {div:.2e}"
