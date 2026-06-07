"""Tests for SemanticCommandChannel — agent->robot coordination."""

from __future__ import annotations

import numpy as np
import pytest

from psl.lod.command_channel import SemanticCommandChannel
from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3
from psl.phyte.provenance import Provenance, ProvenanceEntry
from psl.world_model.core import WorldModel


def _make_command(target_pos: tuple[float, float, float], timestamp: float) -> Phyte:
    return Phyte(
        semantic_id="command:move_to",
        frame="world",
        pose=identity_se3(),
        timestamp=timestamp,
        clock_domain="agent",
        unit="m",
        value=np.array(target_pos),
        covariance=np.eye(3) * 0.01,
        provenance=Provenance(
            chain=[ProvenanceEntry(source="agent", operation="plan", timestamp=timestamp)],
            confidence=0.9,
        ),
    )


@pytest.mark.unit
class TestSemanticCommandChannel:
    def test_issue_and_poll(self) -> None:
        wm = WorldModel()
        wm.register_entity("world")
        wm.register_entity("robot_arm", parent_id="world")
        ch = SemanticCommandChannel(wm)

        cmd = _make_command((0.5, 0.3, 0.8), timestamp=1.0)
        result = ch.issue_command("agent_0", "robot_arm", cmd)
        assert result.accepted

        pending = ch.poll_commands("robot_arm")
        assert len(pending) == 1
        assert pending[0].semantic_id == "command:move_to"

    def test_poll_empty_when_no_commands(self) -> None:
        wm = WorldModel()
        wm.register_entity("world")
        wm.register_entity("robot_arm", parent_id="world")
        ch = SemanticCommandChannel(wm)

        pending = ch.poll_commands("robot_arm")
        assert len(pending) == 0

    def test_acknowledge_removes_from_pending(self) -> None:
        wm = WorldModel()
        wm.register_entity("world")
        wm.register_entity("robot_arm", parent_id="world")
        ch = SemanticCommandChannel(wm)

        cmd = _make_command((1.0, 0.0, 0.5), timestamp=2.0)
        ch.issue_command("agent_0", "robot_arm", cmd)

        pending = ch.poll_commands("robot_arm")
        assert len(pending) == 1

        # Acknowledge
        cmd_key = f"cmd:agent_0:{cmd.timestamp}"
        ch.acknowledge_command("robot_arm", cmd_key)

        pending = ch.poll_commands("robot_arm")
        assert len(pending) == 0

    def test_multiple_commands_ordered_by_timestamp(self) -> None:
        wm = WorldModel()
        wm.register_entity("world")
        wm.register_entity("robot_arm", parent_id="world")
        ch = SemanticCommandChannel(wm)

        ch.issue_command("agent_0", "robot_arm", _make_command((1, 0, 0), timestamp=3.0))
        ch.issue_command("agent_0", "robot_arm", _make_command((0, 1, 0), timestamp=1.0))
        ch.issue_command("agent_0", "robot_arm", _make_command((0, 0, 1), timestamp=2.0))

        pending = ch.poll_commands("robot_arm")
        assert len(pending) == 3
        assert pending[0].timestamp == 1.0
        assert pending[1].timestamp == 2.0
        assert pending[2].timestamp == 3.0

    def test_full_loop_agent_to_robot(self) -> None:
        """Full coordination loop: agent issues command, robot reads and acks."""
        from psl.adapters.robots.panda.adapter import PandaAdapter  # noqa: F401

        wm = WorldModel()
        wm.register_entity("world")
        wm.register_entity("panda_arm", parent_id="world")
        ch = SemanticCommandChannel(wm)

        # Agent issues move command
        target = (0.4, 0.2, 0.6)
        cmd = _make_command(target, timestamp=5.0)
        result = ch.issue_command("supervisor", "panda_arm", cmd)
        assert result.accepted

        # Robot polls
        cmds = ch.poll_commands("panda_arm")
        assert len(cmds) == 1

        # Robot reads target position from command Phyte
        target_pos = cmds[0].value
        np.testing.assert_allclose(target_pos, target)
