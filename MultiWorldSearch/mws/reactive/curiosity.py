"""Curiosity engine — detects observation gaps and emits exploration tasks."""

from __future__ import annotations

from typing import Any

from mws.core.atom import ExperienceAtom
from mws.core.logging import get_logger

logger = get_logger(__name__)


class GapDescriptor:
    """Describes a detected observation gap."""

    def __init__(self, region: str, entity_id: str = "", reason: str = "") -> None:
        self.region = region
        self.entity_id = entity_id
        self.reason = reason


class CuriosityEngine:
    """Detects retrieval gaps and emits exploration tasks.

    A gap is detected when a spatiotemporal region has no recent observations.
    The engine emits exploration tasks to fill these gaps.
    """

    def __init__(self) -> None:
        self._gaps: list[GapDescriptor] = []
        self._exploration_tasks: list[dict[str, Any]] = []

    def detect_gap(
        self,
        region: str,
        atoms: list[ExperienceAtom],
        required_modalities: list[str] | None = None,
    ) -> GapDescriptor | None:
        """Check if a region has observation coverage. Returns GapDescriptor if gap found."""
        region_atoms = [
            a
            for a in atoms
            if any(region in t for t in a.tags) or a.structured_fields.get("region") == region
        ]

        if required_modalities:
            region_atoms = [a for a in region_atoms if a.modality in required_modalities]

        if len(region_atoms) == 0:
            gap = GapDescriptor(region=region, reason=f"No observations in {region}")
            self._gaps.append(gap)
            logger.info("Curiosity: gap detected", region=region)
            return gap
        return None

    def emit_exploration_task(self, gap: GapDescriptor, robot_id: str) -> dict[str, Any]:
        """Emit an exploration task to fill a detected gap."""
        task = {
            "type": "exploration",
            "target_region": gap.region,
            "assigned_robot": robot_id,
            "reason": gap.reason,
        }
        self._exploration_tasks.append(task)
        logger.info(
            "Curiosity: exploration task emitted",
            region=gap.region,
            robot=robot_id,
        )
        return task

    @property
    def gaps_detected(self) -> int:
        return len(self._gaps)

    @property
    def tasks_emitted(self) -> int:
        return len(self._exploration_tasks)
