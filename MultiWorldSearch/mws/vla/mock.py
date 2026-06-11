"""Mock VLA — deterministic action selection for testing."""

from __future__ import annotations

from typing import Any

import numpy as np


class MockVLA:
    """Deterministic VLA for testing the retrieval-augmented pipeline."""

    def __init__(self, seed: int = 0) -> None:
        self._rng = np.random.default_rng(seed)

    def select_action(
        self,
        observation: dict[str, Any],
        retrieved_skills: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Select an action deterministically."""
        return {
            "action_vector": self._rng.standard_normal(6).tolist(),
            "confidence": 0.9,
            "skill_used": len(retrieved_skills) if retrieved_skills else 0,
        }
