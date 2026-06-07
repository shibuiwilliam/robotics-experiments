"""Multi-resolution access / LOD (mechanism 7).

Provides the same world-model data at different abstraction levels:
  - RAW: full Phytes with all fields (for control loops)
  - SUMMARY: aggregated statistics (for mid-frequency monitoring)
  - SEMANTIC: natural-language-compatible descriptions (for agents)

This absorbs the timescale gap: high-frequency consumers get RAW,
low-frequency agents get SEMANTIC, and the LOD system handles downsampling.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

import numpy as np

from psl.phyte.core import Phyte


class ResolutionLevel(Enum):
    """Supported resolution levels for world model queries."""

    RAW = "raw"  # Full Phyte — kHz-level detail
    SUMMARY = "summary"  # Aggregated stats — Hz-level
    SEMANTIC = "semantic"  # Text descriptions — agent-level


@dataclass(frozen=True)
class SummaryView:
    """Aggregated summary of an entity's state.

    Provides mean values, ranges, and a staleness indicator
    without the full Phyte detail.

    Fields:
        entity_id: Entity this summary describes.
        n_joints: Number of joint Phytes.
        joint_pos_mean: Mean joint position (rad).
        joint_pos_range: Max - min joint position spread.
        ee_position: End-effector position [x, y, z] if available.
        timestamp: Latest timestamp across all Phytes.
        staleness_s: Seconds since the newest Phyte's timestamp.
        confidence_min: Minimum confidence across all Phytes.
    """

    entity_id: str
    n_joints: int
    joint_pos_mean: float
    joint_pos_range: float
    ee_position: tuple[float, float, float] | None
    timestamp: float
    staleness_s: float
    confidence_min: float


@dataclass(frozen=True)
class SemanticView:
    """Agent-friendly text description of an entity's state.

    Fields:
        entity_id: Entity this description covers.
        description: Human/agent-readable state summary.
        affordances: Known action possibilities.
        timestamp: When this view was generated.
    """

    entity_id: str
    description: str
    affordances: list[str]
    timestamp: float


class LODSubscriber:
    """Multi-resolution subscriber to the world model.

    Wraps a world model read and presents it at the requested
    resolution level. Decouples consumers from the full Phyte detail.

    Args:
        current_time_fn: Callable returning the current sim time (for staleness).
    """

    def __init__(self, current_time_fn: Callable[[], float] | None = None) -> None:
        self._time_fn = current_time_fn

    def _current_time(self) -> float:
        if callable(self._time_fn):
            return float(self._time_fn())
        return 0.0

    def to_raw(self, phytes: dict[str, Phyte]) -> dict[str, Phyte]:
        """RAW level — return Phytes unchanged.

        Args:
            phytes: Named Phyte dict from world model.

        Returns:
            Same dict (identity at this level).
        """
        return dict(phytes)

    def to_summary(self, entity_id: str, phytes: dict[str, Phyte]) -> SummaryView:
        """SUMMARY level — aggregate statistics from Phytes.

        Args:
            entity_id: Entity being summarized.
            phytes: Named Phyte dict.

        Returns:
            SummaryView with aggregated stats.
        """
        joint_vals: list[float] = []
        timestamps: list[float] = []
        confidences: list[float] = []
        ee_pos: tuple[float, float, float] | None = None

        for name, phyte in phytes.items():
            timestamps.append(phyte.timestamp)
            confidences.append(phyte.provenance.confidence)

            if name.startswith("joint_") and not name.startswith("joint_vel_"):
                joint_vals.append(float(phyte.value[0]))

            if name == "ee_pose" and phyte.value.size >= 3:
                ee_pos = (float(phyte.value[0]), float(phyte.value[1]), float(phyte.value[2]))

        now = self._current_time()
        latest_ts = max(timestamps) if timestamps else 0.0

        return SummaryView(
            entity_id=entity_id,
            n_joints=len(joint_vals),
            joint_pos_mean=float(np.mean(joint_vals)) if joint_vals else 0.0,
            joint_pos_range=float(np.ptp(joint_vals)) if joint_vals else 0.0,
            ee_position=ee_pos,
            timestamp=latest_ts,
            staleness_s=now - latest_ts if now > 0 else 0.0,
            confidence_min=min(confidences) if confidences else 0.0,
        )

    def to_semantic(self, entity_id: str, phytes: dict[str, Phyte]) -> SemanticView:
        """SEMANTIC level — agent-readable text description.

        Args:
            entity_id: Entity being described.
            phytes: Named Phyte dict.

        Returns:
            SemanticView with text description and affordances.
        """
        summary = self.to_summary(entity_id, phytes)
        parts: list[str] = [f"Entity '{entity_id}'"]

        if summary.n_joints > 0:
            parts.append(
                f"has {summary.n_joints} joints (mean pos {summary.joint_pos_mean:.3f} rad)"
            )

        if summary.ee_position is not None:
            x, y, z = summary.ee_position
            parts.append(f"EE at ({x:.3f}, {y:.3f}, {z:.3f}) m")

        if summary.confidence_min < 0.5:
            parts.append("(low confidence)")

        affordances: list[str] = []
        if summary.n_joints > 0:
            affordances.extend(["movable", "controllable"])
        if summary.ee_position is not None:
            affordances.append("reachable")

        return SemanticView(
            entity_id=entity_id,
            description="; ".join(parts),
            affordances=affordances,
            timestamp=summary.timestamp,
        )
