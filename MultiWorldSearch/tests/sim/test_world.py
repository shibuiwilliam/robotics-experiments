"""Tests for MuJoCo world wrapper."""

from mws.sim.world import MuJoCoWorld


def test_world_creation_fallback() -> None:
    """Test that the world creates a minimal model when XML is missing."""
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    assert world.nq > 0


def test_world_step() -> None:
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    world.step(10)
    assert world.step_count == 10
    assert world.time > 0


def test_world_reset() -> None:
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    world.step(10)
    world.reset()
    assert world.step_count == 0


def test_get_body_positions() -> None:
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    positions = world.get_body_positions()
    assert len(positions) > 0
    assert "conveyor_motor" in positions


def test_get_sensor_data() -> None:
    world = MuJoCoWorld(xml_path="nonexistent.xml", seed=0)
    world.step(1)
    sensors = world.get_sensor_data()
    assert "motor_angle" in sensors or "motor_velocity" in sensors


def test_seed_perturbation_is_reproducible_and_seed_dependent() -> None:
    """With qpos_noise > 0, the seed reproducibly perturbs the MuJoCo state:
    same seed → identical rollout, different seed → different rollout."""
    import numpy as np

    def rollout(seed: int) -> np.ndarray:
        w = MuJoCoWorld(xml_path="nonexistent.xml", seed=seed, qpos_noise=0.05)
        w.step(20)
        return w.get_qpos()

    same_a = rollout(7)
    same_b = rollout(7)
    diff = rollout(8)
    assert np.allclose(same_a, same_b)  # reproducible under a fixed seed
    assert not np.allclose(same_a, diff)  # seed genuinely changes the rollout


def test_default_no_perturbation_is_seed_independent() -> None:
    """Default (qpos_noise=0) preserves prior behaviour: the deterministic
    mj_step rollout is identical regardless of seed."""
    import numpy as np

    a = MuJoCoWorld(xml_path="nonexistent.xml", seed=1)
    b = MuJoCoWorld(xml_path="nonexistent.xml", seed=2)
    a.step(20)
    b.step(20)
    assert np.allclose(a.get_qpos(), b.get_qpos())
