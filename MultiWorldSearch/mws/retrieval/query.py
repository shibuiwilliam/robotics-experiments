"""Query model and query planner for multi-index retrieval."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mws.core.types import ConsumerType


class RetrievalQuery(BaseModel):
    """A multi-index retrieval query."""

    text: str = Field(default="", description="Text query for semantic search")
    position: tuple[float, float, float] | None = Field(
        default=None, description="Spatial query center"
    )
    spatial_radius: float = Field(default=5.0, description="Spatial search radius")
    time_start: float | None = Field(default=None, description="Temporal range start")
    time_end: float | None = Field(default=None, description="Temporal range end")
    tags: list[str] = Field(default_factory=list, description="Symbolic tag filter")
    structured_filters: dict[str, str | float | int | bool] = Field(
        default_factory=dict, description="Structured field filters"
    )
    consumer: ConsumerType = Field(default=ConsumerType.LLM, description="Who is consuming results")
    top_k: int = Field(default=10, description="Number of results to return")


class RetrievalResult(BaseModel):
    """A single retrieval result with fused score."""

    atom_id: str
    score: float = Field(description="Fused relevance score")
    scores_by_index: dict[str, float] = Field(default_factory=dict, description="Per-index scores")
    text_summary: str = ""
    modality: str = ""
