"""Entity nodes in the 4D scene graph."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mws.core.types import EntityKind


class EntityNode(BaseModel):
    """A node in the 4D scene graph: object, place, agent, or zone.

    Atoms are attached to entities, grounding observations/documents/records
    to the same real-world thing.
    """

    entity_id: str
    kind: EntityKind
    name: str = ""
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    properties: dict[str, str | float | int | bool] = Field(default_factory=dict)
    atom_ids: list[str] = Field(default_factory=list, description="Attached atom IDs")

    def attach_atom(self, atom_id: str) -> None:
        """Attach an atom to this entity."""
        if atom_id not in self.atom_ids:
            self.atom_ids.append(atom_id)

    def detach_atom(self, atom_id: str) -> None:
        """Detach an atom from this entity."""
        self.atom_ids = [a for a in self.atom_ids if a != atom_id]
