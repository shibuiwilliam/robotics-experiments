"""Shared World Model — blackboard-style hub.

All entities read/write through this shared digital twin.
The three data flows (R2R, A2R, A2A) reduce to "interaction with the world model".

Current implementation: in-memory scene graph (dict-backed tree of entities).
Interface designed so a future USD backend can slot in without changing callers.

IMPORTANT: All writes pass through the physics consistency gate.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from psl.phyte.core import Phyte
from psl.safety.gate import GateResult, PhysicsConsistencyGate


@dataclass
class Entity:
    """A node in the world model scene graph.

    Fields:
        entity_id: Unique identifier.
        parent_id: Parent entity ID (None for root entities).
        phytes: Named Phyte state for this entity.
        children: Child entity IDs.
        metadata: Arbitrary metadata (e.g., mesh ref, type).
    """

    entity_id: str
    parent_id: str | None = None
    phytes: dict[str, Phyte] = field(default_factory=dict)
    children: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


class WorldModel:
    """Shared world model — the central scene graph.

    Thread-safety: protected by RLock for concurrent access.
    The interface is designed to be replaceable with a concurrent backend.

    Args:
        safety_gates: Map of entity_id → PhysicsConsistencyGate.
            If an entity has no gate, writes are accepted unconditionally.
    """

    def __init__(self, safety_gates: dict[str, PhysicsConsistencyGate] | None = None) -> None:
        self._lock = threading.RLock()
        self._entities: dict[str, Entity] = {}
        self._safety_gates = safety_gates or {}
        self._write_log: list[dict[str, object]] = []

    def register_entity(
        self,
        entity_id: str,
        parent_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Entity:
        """Register a new entity in the world model.

        Args:
            entity_id: Unique identifier.
            parent_id: Optional parent entity.
            metadata: Arbitrary metadata.

        Returns:
            The newly created Entity.

        Raises:
            ValueError: If entity_id already exists.
        """
        with self._lock:
            if entity_id in self._entities:
                msg = f"Entity '{entity_id}' already registered"
                raise ValueError(msg)

            entity = Entity(
                entity_id=entity_id,
                parent_id=parent_id,
                metadata=metadata or {},
            )
            self._entities[entity_id] = entity

            if parent_id and parent_id in self._entities:
                self._entities[parent_id].children.append(entity_id)

            return entity

    def write(
        self,
        entity_id: str,
        phytes: dict[str, Phyte],
    ) -> GateResult:
        """Write Phyte state to an entity, passing through the safety gate.

        Args:
            entity_id: Target entity.
            phytes: Named Phytes to write.

        Returns:
            GateResult indicating acceptance/rejection.

        Raises:
            KeyError: If entity_id is not registered.
        """
        with self._lock:
            if entity_id not in self._entities:
                msg = f"Entity '{entity_id}' not registered"
                raise KeyError(msg)

            entity = self._entities[entity_id]
            gate = self._safety_gates.get(entity_id)

            if gate is not None:
                previous = entity.phytes if entity.phytes else None
                result = gate.check(phytes, previous)
                if not result.accepted:
                    self._write_log.append(
                        {
                            "entity_id": entity_id,
                            "accepted": False,
                            "violations": result.violations,
                            "wall_time": time.time(),
                        }
                    )
                    return result

            entity.phytes = dict(phytes)
            self._write_log.append(
                {
                    "entity_id": entity_id,
                    "accepted": True,
                    "violations": [],
                    "wall_time": time.time(),
                }
            )
            return GateResult(accepted=True, violations=[])

    def read(self, entity_id: str) -> dict[str, Phyte]:
        """Read the current Phyte state of an entity.

        Args:
            entity_id: Target entity.

        Returns:
            Dict of named Phytes (may be empty).

        Raises:
            KeyError: If entity_id is not registered.
        """
        with self._lock:
            if entity_id not in self._entities:
                msg = f"Entity '{entity_id}' not registered"
                raise KeyError(msg)
            return dict(self._entities[entity_id].phytes)

    def get_entity(self, entity_id: str) -> Entity:
        """Get the full Entity object.

        Raises:
            KeyError: If entity_id is not registered.
        """
        with self._lock:
            if entity_id not in self._entities:
                msg = f"Entity '{entity_id}' not registered"
                raise KeyError(msg)
            return self._entities[entity_id]

    def list_entities(self) -> list[str]:
        """Return all registered entity IDs."""
        with self._lock:
            return list(self._entities.keys())

    @property
    def write_log(self) -> list[dict[str, object]]:
        """Access the write log (for evaluation/debugging)."""
        return list(self._write_log)

    def register_safety_gate(self, entity_id: str, gate: PhysicsConsistencyGate) -> None:
        """Register or replace the safety gate for an entity."""
        with self._lock:
            self._safety_gates[entity_id] = gate
