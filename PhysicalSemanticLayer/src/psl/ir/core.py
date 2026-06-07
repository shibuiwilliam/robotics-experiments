"""Canonical Intermediate Representation (IR).

All translation goes through the IR — no direct A↔B adapters.
Each robot/agent implements only "native ↔ IR" (N+N, not N×N).

IRState is the canonical scene snapshot: a collection of Phytes in a
consistent frame (world), consistent units (SI), consistent clock domain.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from psl.phyte.core import Phyte


class IRState(BaseModel):
    """Canonical scene state — the IR's representation of the world.

    All Phytes are expressed in:
      - frame: 'world' (or a specified canonical frame)
      - units: SI
      - clock_domain: 'sim' (or the canonical clock)

    Fields:
        entity_id: Unique identifier for the entity this state belongs to.
        phytes: Named collection of Phytes (e.g., 'joint_0', 'ee_pose').
        timestamp: Canonical timestamp for this snapshot.
        clock_domain: Canonical clock domain.
    """

    entity_id: str = Field(description="Entity this state belongs to")
    phytes: dict[str, Phyte] = Field(
        default_factory=dict, description="Named Phytes in canonical form"
    )
    timestamp: float = Field(description="Canonical timestamp")
    clock_domain: str = Field(default="sim", description="Canonical clock domain")

    model_config = {"arbitrary_types_allowed": True, "frozen": True}


@runtime_checkable
class Adapter(Protocol):
    """Protocol for native ↔ IR adapters.

    Each robot/agent implements exactly one Adapter.
    The adapter converts between native representation and IRState.
    """

    @property
    def entity_id(self) -> str:
        """Unique entity identifier for this adapter."""
        ...

    def to_ir(self, native_state: dict[str, object]) -> IRState:
        """Convert native state to canonical IR.

        Args:
            native_state: Robot/agent-specific state dict.

        Returns:
            IRState in canonical form (world frame, SI units).
        """
        ...

    def from_ir(self, ir_state: IRState) -> dict[str, object]:
        """Convert canonical IR back to native state.

        Args:
            ir_state: Canonical IRState.

        Returns:
            Robot/agent-specific state dict.
        """
        ...
