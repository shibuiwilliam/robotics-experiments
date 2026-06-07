"""Semantic negotiation / handshake (mechanism 8).

When a robot or agent joins, it registers a capability descriptor.
The negotiation module resolves which translations are possible
between participants and builds the translation graph.

This is the runtime equivalent of a protocol handshake —
participants exchange what they can express, and the system
determines the intersection of shared meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ControlMode(Enum):
    """Control modes a robot may support."""

    POSITION = "position"
    VELOCITY = "velocity"
    TORQUE = "torque"


@dataclass(frozen=True)
class CapabilityDescriptor:
    """What a participant can express and accept.

    Registered at join time. The negotiation module uses these
    to determine translation feasibility.

    Fields:
        entity_id: Unique identifier.
        entity_type: 'robot' or 'agent'.
        n_joints: Number of joints (0 for agents).
        control_modes: Supported control modes.
        frame_convention: e.g. 'z_up' or 'y_up'.
        unit_system: e.g. 'SI' or 'imperial'.
        sensor_types: Available sensor modalities.
        semantic_capabilities: High-level capabilities (e.g. 'grasp', 'navigate').
    """

    entity_id: str
    entity_type: str  # 'robot' or 'agent'
    n_joints: int = 0
    control_modes: tuple[ControlMode, ...] = (ControlMode.POSITION,)
    frame_convention: str = "z_up"
    unit_system: str = "SI"
    sensor_types: tuple[str, ...] = ("joint_position", "joint_velocity")
    semantic_capabilities: tuple[str, ...] = ()


@dataclass
class NegotiationResult:
    """Result of a semantic handshake between two participants.

    Fields:
        feasible: Whether translation between them is possible.
        shared_modalities: Sensor types both participants have.
        translation_notes: Warnings about lossy aspects.
    """

    feasible: bool
    shared_modalities: list[str] = field(default_factory=list)
    translation_notes: list[str] = field(default_factory=list)


class SemanticNegotiator:
    """Registry of participant capabilities + handshake resolution.

    Participants register at join time. The negotiator determines
    which pairs can communicate and what translations are needed.
    """

    def __init__(self) -> None:
        self._registry: dict[str, CapabilityDescriptor] = {}

    def register(self, descriptor: CapabilityDescriptor) -> None:
        """Register a participant's capabilities.

        Args:
            descriptor: The participant's capability descriptor.
        """
        self._registry[descriptor.entity_id] = descriptor

    def unregister(self, entity_id: str) -> None:
        """Remove a participant from the registry."""
        self._registry.pop(entity_id, None)

    def get(self, entity_id: str) -> CapabilityDescriptor | None:
        """Look up a participant's capabilities."""
        return self._registry.get(entity_id)

    def list_participants(self) -> list[str]:
        """List all registered entity IDs."""
        return list(self._registry.keys())

    def negotiate(self, entity_a: str, entity_b: str) -> NegotiationResult:
        """Perform semantic handshake between two participants.

        Determines whether translation is feasible and identifies
        shared modalities and potential issues.

        Args:
            entity_a: First participant ID.
            entity_b: Second participant ID.

        Returns:
            NegotiationResult with feasibility and details.
        """
        desc_a = self._registry.get(entity_a)
        desc_b = self._registry.get(entity_b)

        if desc_a is None or desc_b is None:
            missing = entity_a if desc_a is None else entity_b
            return NegotiationResult(
                feasible=False,
                translation_notes=[f"Entity '{missing}' not registered"],
            )

        notes: list[str] = []
        shared = list(set(desc_a.sensor_types) & set(desc_b.sensor_types))

        # Frame convention mismatch
        if desc_a.frame_convention != desc_b.frame_convention:
            notes.append(
                f"Frame mismatch: {desc_a.frame_convention} vs {desc_b.frame_convention} "
                "— frame transform required"
            )

        # Unit system mismatch
        if desc_a.unit_system != desc_b.unit_system:
            notes.append(
                f"Unit mismatch: {desc_a.unit_system} vs {desc_b.unit_system} "
                "— unit conversion required"
            )

        # Control mode mismatch
        shared_modes = set(desc_a.control_modes) & set(desc_b.control_modes)
        if not shared_modes:
            notes.append("No shared control modes — mode conversion required")

        # Joint count mismatch (only for robot-robot)
        if (
            desc_a.entity_type == "robot"
            and desc_b.entity_type == "robot"
            and desc_a.n_joints != desc_b.n_joints
        ):
            notes.append(
                f"Joint count mismatch: {desc_a.n_joints} vs {desc_b.n_joints} "
                "— kinematic mapping required"
            )

        # Translation is feasible if both are registered and at least one modality is shared
        # (or one is an agent, which always operates at the semantic level)
        feasible = True
        if desc_a.entity_type == "robot" and desc_b.entity_type == "robot" and not shared:
            feasible = False
            notes.append("No shared sensor modalities between robots")

        return NegotiationResult(
            feasible=feasible,
            shared_modalities=shared,
            translation_notes=notes,
        )
