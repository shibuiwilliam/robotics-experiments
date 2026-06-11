"""Experience Atom — the universal unit of memory in MWS.

An atom = envelope (metadata: spatiotemporal coords, provenance, modality,
embedding_space, trust/freshness, access policy) + modality-specific payload reference.
Heavy data (video, pointclouds) is stored externally; the atom carries references,
summaries, and embeddings.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from mws.core.provenance import ProvenanceChain
from mws.core.types import EmbeddingSpace, Modality


class SpatiotemporalCoord(BaseModel):
    """3D position + timestamp for grounding an atom in the 4D world."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    timestamp: float = Field(description="Seconds since epoch")
    world_id: str = Field(default="default", description="Which simulation world")
    frame_id: str = Field(default="world", description="Reference frame")


class AccessPolicy(BaseModel):
    """Attribute-based access control stub."""

    owner: str = "system"
    allowed_consumers: list[str] = Field(default_factory=lambda: ["*"])
    classification: str = "unclassified"


class EmbeddingRecord(BaseModel):
    """An embedding vector with its space tag. Never mix spaces in one index."""

    space: EmbeddingSpace
    vector: list[float] = Field(description="Embedding vector")
    content_hash: str = Field(default="", description="Hash of embedded content for caching")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ExperienceAtom(BaseModel):
    """The universal memory unit of MWS.

    Envelope fields provide metadata for indexing across all five index types
    (spatial, temporal, semantic, symbolic, structured). The payload_ref points
    to heavy data stored in the blob store.
    """

    atom_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    modality: Modality
    coord: SpatiotemporalCoord
    provenance: ProvenanceChain = Field(default_factory=ProvenanceChain)
    trust: float = Field(default=1.0, ge=0.0, le=1.0, description="Trust score")
    freshness: float = Field(default=1.0, ge=0.0, le=1.0, description="Freshness score (decays)")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Embedding(s) — tagged with space
    embeddings: list[EmbeddingRecord] = Field(default_factory=list)

    # Access control
    access: AccessPolicy = Field(default_factory=AccessPolicy)

    # Entity linkage — connects atom to scene graph node
    entity_id: str | None = Field(default=None, description="Scene graph entity this atom is about")

    # Payload: lightweight structured data inline; heavy data via ref
    payload: dict = Field(default_factory=dict, description="Lightweight payload (structured data)")
    payload_ref: str | None = Field(default=None, description="Blob store reference for heavy data")

    # Text summary for semantic indexing
    text_summary: str = Field(default="", description="Text summary for embedding/search")

    # Tags for symbolic/structured search
    tags: list[str] = Field(default_factory=list)
    structured_fields: dict[str, str | float | int | bool] = Field(
        default_factory=dict,
        description="Typed fields for structured queries (e.g. equipment_id, severity)",
    )

    def get_embedding(self, space: EmbeddingSpace) -> np.ndarray | None:
        """Get embedding vector for the given space, or None."""
        for rec in self.embeddings:
            if rec.space == space:
                return np.array(rec.vector, dtype=np.float32)
        return None

    def add_embedding(self, space: EmbeddingSpace, vector: list[float], content_hash: str) -> None:
        """Add or replace an embedding for the given space."""
        self.embeddings = [e for e in self.embeddings if e.space != space]
        self.embeddings.append(
            EmbeddingRecord(space=space, vector=vector, content_hash=content_hash)
        )
