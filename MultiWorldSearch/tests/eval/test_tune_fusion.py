"""Fusion-weight tuning harness — structure and pre-registration tests."""

from __future__ import annotations

from mws.eval.tune_fusion import CANDIDATE_WEIGHTS, CURRENT_WEIGHTS, run_fusion_tuning
from mws.retrieval.engine import ALL_INDICES


def test_grid_is_pre_registered_and_covers_all_indices() -> None:
    labels = [label for label, _ in CANDIDATE_WEIGHTS]
    assert labels[0] == "current", "current default must be swept first"
    assert len(labels) == len(set(labels))
    for _, weights in CANDIDATE_WEIGHTS:
        assert set(weights) == ALL_INDICES, "every candidate must cover all indices"


def test_current_weights_match_engine_default() -> None:
    from mws.core.types import EmbeddingSpace
    from mws.embedding.mock import MockEmbedder
    from mws.retrieval.engine import RetrievalEngine
    from mws.storage.registry import StoreRegistry

    engine = RetrievalEngine(
        stores=StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=128),
        embedder=MockEmbedder(seed=0),
    )
    assert engine.fusion_weights == CURRENT_WEIGHTS


def test_sweep_renders_pre_registered_verdict() -> None:
    """One-seed sweep runs end-to-end and the decision rule is applied:
    every admissible winner must improve R@5 with zero regressions."""
    result = run_fusion_tuning(seeds=(0,))
    assert result["n_eval_pairs"] == 4  # S1/S3/S5/S7 x 1 seed
    assert result["pre_registered"]["rule"].startswith("adopt iff")
    assert len(result["sweep"]) == len(CANDIDATE_WEIGHTS)
    current_row = result["sweep"][0]
    assert current_row["label"] == "current"
    assert current_row["improves_r5"] is False  # cannot beat itself
    verdict = result["verdict"]
    if verdict["adopt"]:
        winner = next(c for c in result["sweep"] if c["label"] == verdict["winner"])
        assert winner["improves_r5"] and not winner["regressions"]
    else:
        assert verdict["winner"] is None
