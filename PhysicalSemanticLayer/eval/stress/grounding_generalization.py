"""Grounding generalization stress test — Phase 4.

Tests whether PSL's VLA-style grounding (embeddings + affordance
prediction) generalizes to unseen objects.  Compares holdout objects
against a known training set using embedding similarity, affordance
coverage, and material diversity.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from psl.grounding.vla_encoder import EMBEDDING_DIM, AffordancePrediction, VLAEncoder

# Known objects (appeared during development / Phase 0-3)
KNOWN_OBJECTS: list[str] = [
    "red_box",
    "blue_cylinder",
    "green_sphere",
    "metal_bracket",
    "plastic_cap",
    "glass_vial",
    "pcb_board",
    "rubber_gasket",
    "steel_bolt",
    "aluminum_plate",
]

# Holdout objects (never seen during development — test generalization)
HOLDOUT_OBJECTS: list[str] = [
    "ceramic_mug",
    "carbon_fiber_panel",
    "copper_pipe",
    "silicone_seal",
    "titanium_screw",
    "foam_block",
    "nylon_washer",
    "brass_fitting",
    "acrylic_lens",
    "wood_dowel",
    "kevlar_strip",
    "teflon_ring",
    "epoxy_block",
    "zinc_plate",
    "fiberglass_tube",
    "cork_stopper",
    "graphite_rod",
    "lead_weight",
    "magnesium_bar",
    "polycarbonate_sheet",
]


@dataclass(frozen=True)
class GeneralizationResult:
    """Result of grounding a single holdout object.

    Fields:
        object_name: Name of the holdout object.
        embedding_norm: L2 norm of the embedding (should be ~1.0 for unit-norm).
        nearest_known: Name of the most similar known object.
        nearest_cosine: Cosine similarity to nearest known object.
        affordance: Predicted affordance for this object.
        has_valid_embedding: Whether embedding has correct dimensionality.
        has_valid_affordance: Whether affordance prediction succeeded.
    """

    object_name: str
    embedding_norm: float
    nearest_known: str
    nearest_cosine: float
    affordance: AffordancePrediction
    has_valid_embedding: bool
    has_valid_affordance: bool


def _cosine_similarity(a: NDArray[np.float64], b: NDArray[np.float64]) -> float:
    """Compute cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def run_generalization(
    holdout_objects: list[str],
    known_objects: list[str],
    seed: int = 42,
) -> list[GeneralizationResult]:
    """Test grounding generalization from known to holdout objects.

    Encodes all known and holdout objects, then measures how well
    each holdout object maps into the known embedding space.

    Args:
        holdout_objects: Object names not seen during development.
        known_objects: Object names from the training/development set.
        seed: Random seed.

    Returns:
        List of GeneralizationResult, one per holdout object.
    """
    encoder = VLAEncoder(use_real_clip=False, seed=seed)

    # Encode known objects
    known_embeddings: dict[str, NDArray[np.float64]] = {}
    for obj in known_objects:
        phyte = encoder.encode_object(obj, timestamp=0.0)
        known_embeddings[obj] = phyte.value

    results: list[GeneralizationResult] = []

    for obj in holdout_objects:
        # Encode holdout object
        phyte = encoder.encode_object(obj, timestamp=0.0)
        embedding = phyte.value
        has_valid_embedding = embedding.shape == (EMBEDDING_DIM,) and np.isfinite(embedding).all()
        embedding_norm = float(np.linalg.norm(embedding))

        # Find nearest known object
        best_name = known_objects[0]
        best_cosine = -1.0
        for known_name, known_emb in known_embeddings.items():
            cos = _cosine_similarity(embedding, known_emb)
            if cos > best_cosine:
                best_cosine = cos
                best_name = known_name

        # Predict affordances
        try:
            affordance = encoder.predict_affordances(obj, timestamp=0.0)
            has_valid_affordance = True
        except Exception:
            affordance = AffordancePrediction(
                graspable=False,
                detachable=False,
                material="unknown",
                embedding=embedding,
                confidence=0.0,
            )
            has_valid_affordance = False

        results.append(
            GeneralizationResult(
                object_name=obj,
                embedding_norm=embedding_norm,
                nearest_known=best_name,
                nearest_cosine=best_cosine,
                affordance=affordance,
                has_valid_embedding=bool(has_valid_embedding),
                has_valid_affordance=bool(has_valid_affordance),
            )
        )

    return results


def compute_generalization_metrics(
    results: list[GeneralizationResult],
) -> dict[str, object]:
    """Compute aggregate generalization metrics.

    Args:
        results: List of GeneralizationResult from test_generalization.

    Returns:
        Dict with summary metrics:
          - embedding_coverage: fraction with valid embeddings
          - affordance_coverage: fraction with valid affordance predictions
          - material_diversity: number of distinct predicted materials
          - mean_nearest_cosine: average cosine similarity to nearest known
          - min_nearest_cosine: minimum cosine similarity
          - max_nearest_cosine: maximum cosine similarity
          - mean_embedding_norm: average embedding L2 norm
    """
    n = len(results)
    if n == 0:
        return {
            "embedding_coverage": 0.0,
            "affordance_coverage": 0.0,
            "material_diversity": 0,
            "mean_nearest_cosine": 0.0,
            "min_nearest_cosine": 0.0,
            "max_nearest_cosine": 0.0,
            "mean_embedding_norm": 0.0,
        }

    embedding_valid = sum(1 for r in results if r.has_valid_embedding)
    affordance_valid = sum(1 for r in results if r.has_valid_affordance)
    materials = {r.affordance.material for r in results}
    cosines = [r.nearest_cosine for r in results]
    norms = [r.embedding_norm for r in results]

    return {
        "embedding_coverage": embedding_valid / n,
        "affordance_coverage": affordance_valid / n,
        "material_diversity": len(materials),
        "mean_nearest_cosine": float(np.mean(cosines)),
        "min_nearest_cosine": float(np.min(cosines)),
        "max_nearest_cosine": float(np.max(cosines)),
        "mean_embedding_norm": float(np.mean(norms)),
    }
