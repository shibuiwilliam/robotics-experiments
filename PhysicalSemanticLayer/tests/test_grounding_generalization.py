"""Tests for grounding generalization stress test."""

from __future__ import annotations

import pytest

from eval.stress.grounding_generalization import (
    HOLDOUT_OBJECTS,
    KNOWN_OBJECTS,
    compute_generalization_metrics,
    run_generalization,
)


@pytest.mark.unit
class TestGroundingGeneralization:
    def test_all_holdout_get_embeddings(self) -> None:
        results = run_generalization(HOLDOUT_OBJECTS, KNOWN_OBJECTS, seed=42)
        assert len(results) == len(HOLDOUT_OBJECTS)
        for r in results:
            assert abs(r.embedding_norm - 1.0) < 1e-5

    def test_holdout_distinct_from_known(self) -> None:
        results = run_generalization(HOLDOUT_OBJECTS, KNOWN_OBJECTS, seed=42)
        for r in results:
            assert r.nearest_cosine < 0.99, (
                f"{r.object_name} too similar to {r.nearest_known}: {r.nearest_cosine:.4f}"
            )

    def test_all_get_affordance_predictions(self) -> None:
        results = run_generalization(HOLDOUT_OBJECTS, KNOWN_OBJECTS, seed=42)
        for r in results:
            assert r.has_valid_affordance
            assert r.affordance.material != ""

    def test_material_diversity(self) -> None:
        results = run_generalization(HOLDOUT_OBJECTS, KNOWN_OBJECTS, seed=42)
        metrics = compute_generalization_metrics(results)
        assert metrics["material_diversity"] > 1

    def test_metrics_valid(self) -> None:
        results = run_generalization(HOLDOUT_OBJECTS, KNOWN_OBJECTS, seed=42)
        metrics = compute_generalization_metrics(results)
        assert metrics["embedding_coverage"] == 1.0
        assert metrics["affordance_coverage"] == 1.0
        assert 0.0 <= float(metrics["mean_nearest_cosine"]) <= 1.0
