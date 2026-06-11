"""Agent protocol and MWS search tool definition."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class SearchToolRequest(BaseModel):
    """Request to the MWS search tool (MCP-compatible interface)."""

    query_text: str = ""
    position: tuple[float, float, float] | None = None
    tags: list[str] = Field(default_factory=list)
    top_k: int = 5


class SearchToolResponse(BaseModel):
    """Response from the MWS search tool."""

    results: list[dict[str, Any]] = Field(default_factory=list)
    total_found: int = 0


class Agent(Protocol):
    """Protocol for MWS agents."""

    def plan(self, context: dict[str, Any]) -> list[str]:
        """Generate a plan of actions given context."""
        ...

    def execute_step(self, step: str, context: dict[str, Any]) -> dict[str, Any]:
        """Execute a single step from the plan."""
        ...
