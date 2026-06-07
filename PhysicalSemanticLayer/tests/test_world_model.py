"""Unit tests for world model and safety gate integration."""

from __future__ import annotations

import threading

import numpy as np
import pytest

from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3, make_se3
from psl.safety.gate import PhysicsConsistencyGate
from psl.world_model.core import WorldModel


def _make_joint_phyte(name: str, value: float, timestamp: float = 0.0) -> Phyte:
    """Helper to create a joint position Phyte."""
    return Phyte(
        semantic_id=f"joint_position_{name}",
        frame="world",
        pose=identity_se3(),
        timestamp=timestamp,
        unit="rad",
        value=np.array([value]),
        covariance=np.array([[1e-8]]),
    )


@pytest.mark.unit
class TestWorldModel:
    def test_register_entity(self) -> None:
        wm = WorldModel()
        entity = wm.register_entity("robot_0")
        assert entity.entity_id == "robot_0"
        assert "robot_0" in wm.list_entities()

    def test_duplicate_registration(self) -> None:
        wm = WorldModel()
        wm.register_entity("robot_0")
        with pytest.raises(ValueError, match="already registered"):
            wm.register_entity("robot_0")

    def test_write_and_read(self) -> None:
        wm = WorldModel()
        wm.register_entity("robot_0")
        phytes = {"joint_0": _make_joint_phyte("0", 0.5)}
        result = wm.write("robot_0", phytes)
        assert result.accepted

        read_back = wm.read("robot_0")
        assert "joint_0" in read_back
        assert float(read_back["joint_0"].value[0]) == pytest.approx(0.5)

    def test_read_unregistered(self) -> None:
        wm = WorldModel()
        with pytest.raises(KeyError):
            wm.read("nonexistent")

    def test_parent_child(self) -> None:
        wm = WorldModel()
        wm.register_entity("world")
        wm.register_entity("robot_0", parent_id="world")
        parent = wm.get_entity("world")
        assert "robot_0" in parent.children


@pytest.mark.unit
class TestSafetyGate:
    def test_accept_valid_joint(self, safety_gate: PhysicsConsistencyGate) -> None:
        state = {"joint_0": _make_joint_phyte("0", 0.0)}
        result = safety_gate.check(state)
        assert result.accepted

    def test_reject_out_of_limits(self, safety_gate: PhysicsConsistencyGate) -> None:
        state = {"joint_0": _make_joint_phyte("0", 5.0)}  # Exceeds Panda limit
        result = safety_gate.check(state)
        assert not result.accepted
        assert len(result.violations) > 0

    def test_teleport_detection(self) -> None:
        gate = PhysicsConsistencyGate(max_linear_velocity=1.0)
        T1 = make_se3(np.eye(3), np.array([0.0, 0.0, 0.0]))
        T2 = make_se3(np.eye(3), np.array([100.0, 0.0, 0.0]))  # 100m teleport

        prev = {
            "ee": Phyte(
                semantic_id="ee",
                frame="world",
                pose=T1,
                timestamp=0.0,
                unit="m",
                value=np.array([0.0, 0.0, 0.0]),
                covariance=np.eye(3) * 0.01,
            )
        }
        new = {
            "ee": Phyte(
                semantic_id="ee",
                frame="world",
                pose=T2,
                timestamp=0.001,  # 1ms later
                unit="m",
                value=np.array([100.0, 0.0, 0.0]),
                covariance=np.eye(3) * 0.01,
            )
        }
        result = gate.check(new, prev)
        assert not result.accepted
        assert any("teleport" in v for v in result.violations)

    def test_world_model_safety_integration(self, safety_gate: PhysicsConsistencyGate) -> None:
        """World model should reject writes that fail the safety gate."""
        wm = WorldModel(safety_gates={"robot_0": safety_gate})
        wm.register_entity("robot_0")

        # Valid write
        valid = {"joint_0": _make_joint_phyte("0", 0.0)}
        result = wm.write("robot_0", valid)
        assert result.accepted

        # Invalid write (out of limits)
        invalid = {"joint_0": _make_joint_phyte("0", 5.0)}
        result = wm.write("robot_0", invalid)
        assert not result.accepted

        # State should not have been updated
        state = wm.read("robot_0")
        assert float(state["joint_0"].value[0]) == pytest.approx(0.0)


@pytest.mark.unit
class TestConcurrentAccess:
    """Thread-safety tests for WorldModel."""

    def test_concurrent_writes_no_corruption(self) -> None:
        """Spawn 10 threads each writing 100 Phytes — no data loss."""
        from psl.phyte.core import Phyte
        from psl.phyte.geometry import identity_se3
        from psl.phyte.provenance import Provenance, ProvenanceEntry

        wm = WorldModel()
        wm.register_entity("world")
        n_threads = 10
        writes_per_thread = 100

        for i in range(n_threads):
            wm.register_entity(f"entity_{i}", parent_id="world")

        errors: list[str] = []

        def writer(thread_id: int) -> None:
            entity_id = f"entity_{thread_id}"
            for j in range(writes_per_thread):
                phyte = Phyte(
                    semantic_id=f"val_{thread_id}_{j}",
                    frame="world",
                    pose=identity_se3(),
                    timestamp=float(j),
                    clock_domain="sim",
                    unit="m",
                    value=np.array([float(thread_id * 1000 + j)]),
                    covariance=np.array([[1.0]]),
                    provenance=Provenance(
                        chain=[
                            ProvenanceEntry(source="test", operation="write", timestamp=float(j))
                        ],
                        confidence=0.99,
                    ),
                )
                result = wm.write(entity_id, {f"v_{j}": phyte})
                if not result.accepted:
                    errors.append(f"Thread {thread_id} write {j} rejected")

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Write errors: {errors}"

        # Verify all entities have data
        for i in range(n_threads):
            state = wm.read(f"entity_{i}")
            assert len(state) > 0

        # Verify write log has all successful writes
        accepted = [e for e in wm.write_log if e["accepted"]]
        assert len(accepted) == n_threads * writes_per_thread

    def test_concurrent_read_write(self) -> None:
        """Concurrent reads and writes don't crash."""
        from psl.phyte.core import Phyte
        from psl.phyte.geometry import identity_se3
        from psl.phyte.provenance import Provenance, ProvenanceEntry

        wm = WorldModel()
        wm.register_entity("world")
        wm.register_entity("target", parent_id="world")

        def make_phyte(val: float) -> Phyte:
            return Phyte(
                semantic_id="test",
                frame="world",
                pose=identity_se3(),
                timestamp=val,
                clock_domain="sim",
                unit="m",
                value=np.array([val]),
                covariance=np.array([[1.0]]),
                provenance=Provenance(
                    chain=[ProvenanceEntry(source="test", operation="w", timestamp=val)],
                    confidence=0.99,
                ),
            )

        errors: list[str] = []

        def writer() -> None:
            for i in range(200):
                wm.write("target", {"v": make_phyte(float(i))})

        def reader() -> None:
            for _ in range(200):
                try:
                    wm.read("target")
                except Exception as e:
                    errors.append(str(e))

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
