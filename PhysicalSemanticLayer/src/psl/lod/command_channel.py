"""Semantic command channel — agent->robot coordination via WorldModel.

Bridges the time-scale gap: agents issue commands at low frequency (seconds),
robots poll at medium frequency. Commands are Phytes stored in the WorldModel
under a namespace, validated by the safety gate.
"""

from __future__ import annotations

import contextlib

from psl.phyte.core import Phyte
from psl.safety.gate import GateResult
from psl.world_model.core import WorldModel


class SemanticCommandChannel:
    """Command channel for agent-to-robot semantic coordination.

    Agents write command Phytes to the WorldModel, robots poll for pending
    commands addressed to them. The safety gate validates command targets.

    Args:
        world_model: Shared world model instance.
    """

    def __init__(self, world_model: WorldModel) -> None:
        self._wm = world_model
        self._pending: dict[str, list[tuple[str, Phyte]]] = {}
        self._acknowledged: set[str] = set()

    def issue_command(
        self,
        agent_entity_id: str,
        target_entity_id: str,
        command_phyte: Phyte,
    ) -> GateResult:
        """Issue a semantic command from an agent to a robot.

        The command Phyte is written to the WorldModel under the
        commands namespace for the target entity.

        Args:
            agent_entity_id: The agent issuing the command.
            target_entity_id: The robot receiving the command.
            command_phyte: The command as a Phyte (e.g., move_to target).

        Returns:
            GateResult from the WorldModel write (safety-validated).
        """
        cmd_key = f"cmd:{agent_entity_id}:{command_phyte.timestamp}"

        # Ensure target entity exists in world model
        if target_entity_id not in self._wm.list_entities():
            # Register commands namespace if needed
            with contextlib.suppress(ValueError):
                self._wm.register_entity(
                    f"commands:{target_entity_id}",
                    parent_id=target_entity_id,
                )

        cmd_entity = f"commands:{target_entity_id}"
        try:
            result = self._wm.write(cmd_entity, {cmd_key: command_phyte})
        except KeyError:
            # Target not registered — register and retry
            self._wm.register_entity(cmd_entity)
            result = self._wm.write(cmd_entity, {cmd_key: command_phyte})

        if result.accepted:
            if target_entity_id not in self._pending:
                self._pending[target_entity_id] = []
            self._pending[target_entity_id].append((cmd_key, command_phyte))

        return result

    def poll_commands(self, entity_id: str) -> list[Phyte]:
        """Poll for pending commands addressed to an entity.

        Args:
            entity_id: The robot polling for commands.

        Returns:
            List of command Phytes ordered by timestamp, excluding acknowledged ones.
        """
        pending = self._pending.get(entity_id, [])
        unacked = [(key, phyte) for key, phyte in pending if key not in self._acknowledged]
        # Sort by timestamp
        unacked.sort(key=lambda x: x[1].timestamp)
        return [phyte for _, phyte in unacked]

    def acknowledge_command(self, entity_id: str, command_id: str) -> None:
        """Mark a command as executed.

        Args:
            entity_id: The robot that executed the command.
            command_id: The command key to acknowledge.
        """
        self._acknowledged.add(command_id)
        # Clean up from pending
        if entity_id in self._pending:
            self._pending[entity_id] = [
                (k, p) for k, p in self._pending[entity_id] if k != command_id
            ]
