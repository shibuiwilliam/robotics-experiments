"""Retrieval-augmented VLA policy — search before acting.

Retrieves skill demonstrations from MWS and uses the actual trajectory data
from the best-matching skill as the action sequence. Falls back to random
actions when no skill is found. This is the single implementation of
"recall a skill, replay its trajectory" used by the scenarios (S1 manipulator,
S4 swarm pick) — scenarios consume its output instead of re-implementing the
replay logic (IMPROVEMENT R6).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from mws.core.logging import get_logger
from mws.core.types import ConsumerType
from mws.retrieval.engine import RetrievalEngine
from mws.retrieval.query import RetrievalQuery

logger = get_logger(__name__)

# Confidence levels: retrieval-backed replay vs blind exploration.
CONFIDENCE_WITH_SKILL = 0.88
CONFIDENCE_EXPLORE = 0.45


class RetrievalAugmentedPolicy:
    """VLA policy that retrieves similar past episodes and skill demos before acting.

    When a matching skill demo is found, uses its trajectory as the action
    sequence. When no skill is found, falls back to random exploration.
    """

    def __init__(
        self,
        retrieval_engine: RetrievalEngine,
        seed: int = 0,
    ) -> None:
        self.engine = retrieval_engine
        self._rng = np.random.default_rng(seed)

    def act(
        self,
        observation: dict[str, Any],
        task: str = "",
        tags: list[str] | None = None,
        structured_filters: dict[str, str | float | int | bool] | None = None,
        top_k: int = 3,
        spatial_radius: float = 5.0,
        action_type: str = "maintenance_action",
    ) -> dict[str, Any]:
        """Generate an action given observation and task.

        1. Retrieves similar past episodes/skill demos from MWS
        2. Extracts trajectory from the best-matching skill atom
        3. Uses retrieved trajectory as action (or falls back to random)

        Args:
            observation: Must contain "position" (xyz tuple).
            task: Text query describing the task.
            tags: Symbolic tags for retrieval (default: skill_demo/maintenance).
            structured_filters: Optional structured filters for the query.
            top_k: Number of retrieval candidates to consider.
            spatial_radius: Spatial search radius around the observation.
            action_type: Label for the returned action dict.
        """
        raw_pos = observation.get("position", (0.0, 0.0, 0.0))
        position: tuple[float, float, float] = (
            float(raw_pos[0]),
            float(raw_pos[1]),
            float(raw_pos[2]),
        )
        query = RetrievalQuery(
            text=task,
            position=position,
            spatial_radius=spatial_radius,
            tags=tags if tags is not None else ["skill_demo", "maintenance"],
            structured_filters=structured_filters or {},
            consumer=ConsumerType.VLA,
            top_k=top_k,
        )
        results = self.engine.search(query)
        projected = self.engine.project(results, query)

        # Try to extract trajectory from the best-matching skill atom
        retrieved_trajectory: list[list[float]] | None = None
        skill_atom_id: str | None = None
        skill_fields: dict[str, Any] | None = None
        for r in results:
            atom = self.engine.get_atom(r.atom_id)
            if atom and atom.payload.get("trajectory"):
                retrieved_trajectory = atom.payload["trajectory"]
                skill_atom_id = atom.atom_id
                skill_fields = dict(atom.structured_fields)
                break

        if retrieved_trajectory and len(retrieved_trajectory) > 0:
            # Use the first step of the retrieved trajectory as the action vector,
            # blended with observation noise for adaptation
            base_action = np.array(retrieved_trajectory[0], dtype=np.float64)
            noise = self._rng.standard_normal(len(base_action)) * 0.01
            action_vector = (base_action + noise).tolist()
            confidence = CONFIDENCE_WITH_SKILL
            used_retrieval = True
        else:
            # No skill found — random exploration
            action_vector = self._rng.standard_normal(6).tolist()
            confidence = CONFIDENCE_EXPLORE
            used_retrieval = False

        # Compute trajectory similarity if we have both
        trajectory_similarity = 0.0
        if retrieved_trajectory and used_retrieval:
            ref = np.array(retrieved_trajectory[0], dtype=np.float64)
            act_vec = np.array(action_vector[: len(ref)], dtype=np.float64)
            norm_ref = np.linalg.norm(ref)
            norm_act = np.linalg.norm(act_vec)
            if norm_ref > 0 and norm_act > 0:
                trajectory_similarity = float(np.dot(ref, act_vec) / (norm_ref * norm_act))

        logger.info(
            "VLA action generated",
            task=task,
            n_results=len(results),
            used_retrieval=used_retrieval,
            trajectory_similarity=f"{trajectory_similarity:.3f}",
        )

        return {
            "type": action_type,
            "target_position": list(position),
            "action_vector": action_vector,
            "retrieved_skills": len(projected),
            "n_results": len(results),
            "confidence": confidence,
            "task": task,
            "used_retrieval": used_retrieval,
            "trajectory_similarity": trajectory_similarity,
            "trajectory": retrieved_trajectory,
            "skill_atom_id": skill_atom_id,
            "skill_fields": skill_fields,
        }
