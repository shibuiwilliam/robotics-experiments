"""Provenance tracking for Phytes.

Every physical quantity must carry its source chain and confidence score.
This module defines the Provenance model used by Phyte.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProvenanceEntry(BaseModel):
    """A single link in the provenance chain."""

    source: str = Field(description="Source identifier (e.g., 'mujoco_sensor', 'panda_adapter')")
    operation: str = Field(
        description="Operation performed (e.g., 'read_joint', 'frame_transform')"
    )
    timestamp: float = Field(description="Wall-clock time when this step occurred (seconds)")

    model_config = {"frozen": True}


class Provenance(BaseModel):
    """Full provenance: ordered chain of transformations + confidence.

    The chain records the path data took from its origin to the current form.
    Confidence is a [0, 1] score reflecting trust in the overall chain.
    """

    chain: list[ProvenanceEntry] = Field(
        default_factory=list, description="Ordered list of provenance entries, oldest first"
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Overall confidence in this data [0, 1]"
    )

    model_config = {"frozen": True}

    def extend(
        self, source: str, operation: str, timestamp: float, confidence_factor: float = 1.0
    ) -> Provenance:
        """Return a new Provenance with an appended entry.

        Args:
            source: Source identifier for the new step.
            operation: What was done.
            timestamp: When the step occurred.
            confidence_factor: Multiplicative factor applied to current confidence.

        Returns:
            New Provenance with the extended chain and updated confidence.
        """
        new_entry = ProvenanceEntry(source=source, operation=operation, timestamp=timestamp)
        new_confidence = max(0.0, min(1.0, self.confidence * confidence_factor))
        return Provenance(chain=[*self.chain, new_entry], confidence=new_confidence)
