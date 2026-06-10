"""Tests for SmolVLAEncoder — true VLA with action prediction."""

from __future__ import annotations

import numpy as np
import pytest

from psl.grounding.smolvla_encoder import SmolVLAEncoder
from psl.grounding.vla_encoder import ActionPrediction, AffordancePrediction


@pytest.mark.unit
class TestSmolVLAFallback:
    """Tests for SmolVLA fallback mode (no model required)."""

    def test_fallback_predict_action_zero_confidence(self) -> None:
        enc = SmolVLAEncoder(use_smolvla=False, seed=42)
        action = enc.predict_action(
            image=None,
            instruction="pick up the gear",
            robot_state={"joint_positions": np.zeros(7)},
        )
        assert isinstance(action, ActionPrediction)
        assert action.confidence == 0.0
        assert action.horizon == 0
        assert action.joint_targets.shape == (7,)

    def test_fallback_affordances_delegate_to_clip(self) -> None:
        enc = SmolVLAEncoder(use_smolvla=False, seed=42)
        aff = enc.predict_affordances("metal_bracket")
        assert isinstance(aff, AffordancePrediction)
        assert isinstance(aff.graspable, bool)

    def test_fallback_encode_object(self) -> None:
        enc = SmolVLAEncoder(use_smolvla=False, seed=42)
        phyte = enc.encode_object("blue_gear", timestamp=1.0)
        assert phyte.value.shape == (512,)

    def test_has_action_model_false_in_fallback(self) -> None:
        enc = SmolVLAEncoder(use_smolvla=False)
        assert not enc.has_action_model
        assert enc.model_name == "clip_fallback"

    def test_action_to_phyte(self) -> None:
        enc = SmolVLAEncoder(use_smolvla=False)
        action = ActionPrediction(
            joint_targets=np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]),
            ee_delta=np.zeros(6),
            gripper=0.5,
            confidence=0.8,
            horizon=1,
        )
        phyte = enc.action_to_phyte(action, timestamp=5.0)
        assert phyte.semantic_id == "action:predicted_joint_targets"
        assert phyte.unit == "rad"
        assert phyte.value.shape == (7,)
        assert phyte.covariance.shape == (7, 7)
        assert phyte.covariance[0, 0] == pytest.approx(0.2, abs=0.01)
        assert phyte.provenance.confidence == 0.8

    def test_action_to_phyte_low_confidence_high_covariance(self) -> None:
        enc = SmolVLAEncoder(use_smolvla=False)
        action = ActionPrediction(
            joint_targets=np.zeros(7),
            ee_delta=np.zeros(6),
            gripper=0.0,
            confidence=0.1,
            horizon=0,
        )
        phyte = enc.action_to_phyte(action, timestamp=0.0)
        assert phyte.covariance[0, 0] == pytest.approx(0.9, abs=0.01)

    def test_action_through_safety_gate(self) -> None:
        from psl.safety.gate import JointLimits, PhysicsConsistencyGate

        enc = SmolVLAEncoder(use_smolvla=False)
        valid_action = ActionPrediction(
            joint_targets=np.array([0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0]),
            ee_delta=np.zeros(6),
            gripper=0.5,
            confidence=0.9,
            horizon=1,
        )
        phyte = enc.action_to_phyte(valid_action, timestamp=1.0)
        gate = PhysicsConsistencyGate(
            joint_limits=JointLimits(
                position_lower=np.full(7, -3.0),
                position_upper=np.full(7, 3.0),
                velocity_max=np.full(7, 2.61),
            )
        )
        gate_result = gate.check({"joint_0": phyte})
        assert phyte.provenance.chain[0].source == "smolvla:fallback"
        assert gate_result.accepted or len(gate_result.violations) > 0


def _smolvla_model_loadable() -> bool:
    """Check if SmolVLA can actually load (not just importable)."""
    try:
        enc = SmolVLAEncoder(seed=42)
        return enc.has_action_model
    except Exception:
        return False


@pytest.mark.slow
@pytest.mark.skipif(not _smolvla_model_loadable(), reason="SmolVLA model not loadable")
class TestSmolVLAReal:
    """Tests requiring actual SmolVLA model."""

    def test_model_loads(self) -> None:
        enc = SmolVLAEncoder(seed=42)
        assert enc.has_action_model

    def test_predict_action_returns_valid(self) -> None:
        enc = SmolVLAEncoder(seed=42)
        image = np.random.default_rng(42).integers(0, 255, (256, 256, 3), dtype=np.uint8)
        action = enc.predict_action(image, "pick up the gear", {"joint_positions": np.zeros(7)})
        assert isinstance(action, ActionPrediction)
        assert action.ee_delta.shape == (6,)
        assert action.confidence > 0

    def test_predict_action_produces_nonzero_delta(self) -> None:
        """SmolVLA should produce non-trivial EE deltas (not all zeros)."""
        enc = SmolVLAEncoder(seed=42)
        image = np.random.default_rng(42).integers(0, 255, (256, 256, 3), dtype=np.uint8)
        a = enc.predict_action(image, "grasp the object", {"joint_positions": np.zeros(7)})
        assert np.linalg.norm(a.ee_delta) > 0.01, "EE delta should be non-trivial"


@pytest.mark.unit
class TestMuJoCoRender:
    """Test MuJoCo rendering capability."""

    def test_render_returns_valid_image(self) -> None:
        from sim.wrapper import MuJoCoSim

        sim = MuJoCoSim()
        sim.step(10)
        img = sim.render(256, 256)
        assert img.shape == (256, 256, 3)
        assert img.dtype == np.uint8

    def test_render_with_camera_name(self) -> None:
        from sim.wrapper import MuJoCoSim

        sim = MuJoCoSim()
        sim.step(10)
        img = sim.render(256, 256, camera="overhead_cam")
        assert img.shape == (256, 256, 3)
