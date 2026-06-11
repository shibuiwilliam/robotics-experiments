"""Ablation study — compare retrieval quality across index subsets.

Answers the question: "Does multi-index fusion actually improve retrieval
over single-index search?" by running the same query with different
combinations of enabled indices and measuring Recall@k, MRR, nDCG.
"""

from __future__ import annotations

from mws.eval.metrics import mrr, ndcg_at_k, recall_at_k
from mws.retrieval.engine import RetrievalEngine
from mws.retrieval.query import RetrievalQuery

# Default ablation configurations: progressively enable more indices
DEFAULT_CONFIGS: list[tuple[str, set[str]]] = [
    ("semantic_only", {"semantic"}),
    ("semantic+spatial", {"semantic", "spatial"}),
    ("semantic+spatial+structured", {"semantic", "spatial", "structured"}),
    ("semantic+spatial+structured+symbolic", {"semantic", "spatial", "structured", "symbolic"}),
    # Control row: everything except relational, so the relational (scene-graph)
    # contribution is directly visible as the delta to "all_indices".
    (
        "all_except_relational",
        {"semantic", "spatial", "temporal", "symbolic", "structured"},
    ),
    ("all_indices", {"semantic", "spatial", "temporal", "symbolic", "structured", "relational"}),
]


def run_ablation(
    engine: RetrievalEngine,
    query: RetrievalQuery,
    relevant_ids: set[str],
    configurations: list[tuple[str, set[str]]] | None = None,
) -> dict[str, dict[str, float]]:
    """Run the same query with different index subsets and compare metrics.

    Args:
        engine: The retrieval engine (must already have atoms ingested).
        query: The query to evaluate.
        relevant_ids: Ground-truth relevant atom IDs.
        configurations: List of (name, enabled_indices) tuples. Defaults to
            progressively enabling indices from semantic-only to all-6.

    Returns:
        {config_name: {recall_at_5, recall_at_10, mrr, ndcg_at_10, n_results}}
    """
    if configurations is None:
        configurations = DEFAULT_CONFIGS

    results: dict[str, dict[str, float]] = {}

    for config_name, enabled_indices in configurations:
        search_results = engine.search_with_indices(query, enabled_indices)
        retrieved_ids = [r.atom_id for r in search_results]

        results[config_name] = {
            "recall_at_5": recall_at_k(retrieved_ids, relevant_ids, k=5),
            "recall_at_10": recall_at_k(retrieved_ids, relevant_ids, k=10),
            "mrr": mrr(retrieved_ids, relevant_ids),
            "ndcg_at_10": ndcg_at_k(retrieved_ids, relevant_ids, k=10),
            "n_results": float(len(search_results)),
            "n_indices": float(len(enabled_indices)),
        }

    return results
