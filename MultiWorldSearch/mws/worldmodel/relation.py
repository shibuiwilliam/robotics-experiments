"""Relations between entities in the 4D scene graph."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mws.core.types import RelationType


class Relation(BaseModel):
    """A typed relation between two entities."""

    source_id: str
    target_id: str
    relation_type: RelationType
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    timestamp: float = Field(default=0.0, description="When this relation was established")
    properties: dict[str, str | float] = Field(default_factory=dict)
