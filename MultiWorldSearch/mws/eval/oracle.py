"""Privileged-oracle differential evaluation — the perception tax (H3).

The simulation privilege (PROJECT.md §5.3) gives us ground-truth relevance
labels. An ORACLE retriever that ranks every relevant atom first is therefore
the upper bound any retrieval pipeline can reach at a given cutoff. The gap
between the oracle's metrics and the real pipeline's metrics is the
"perception tax" (PROJECT.md §6-1): the retrieval quality lost to imperfect
description, embedding, and fusion.
"""

from __future__ import annotations

from mws.core.atom import ExperienceAtom
from mws.scenarios.ground_truth import compute_retrieval_metrics


def oracle_ranking(atoms: list[ExperienceAtom], relevant_ids: set[str]) -> list[str]:
    """Rank all atoms with ground-truth relevance first (deterministic order).

    This is the privileged upper-bound retriever: it cannot exist outside
    simulation because it reads the true labels directly.
    """
    relevant = sorted(a.atom_id for a in atoms if a.atom_id in relevant_ids)
    rest = sorted(a.atom_id for a in atoms if a.atom_id not in relevant_ids)
    return relevant + rest


def perception_tax(
    atoms: list[ExperienceAtom],
    relevant_ids: set[str],
    pipeline_metrics: dict[str, float],
) -> dict[str, dict[str, float]]:
    """Measure the oracle↔pipeline retrieval-quality gap per metric.

    Returns {"oracle": {...}, "tax": {metric: oracle - pipeline}} for the
    metrics shared with the pipeline (recall/mrr/ndcg). A tax of 0 means the
    pipeline already retrieves at the simulation-privileged upper bound.
    """
    oracle_ids = oracle_ranking(atoms, relevant_ids)
    oracle_metrics = compute_retrieval_metrics(oracle_ids, relevant_ids)
    tax = {
        name: round(oracle_metrics[name] - pipeline_metrics.get(name, 0.0), 6)
        for name in oracle_metrics
        if name in pipeline_metrics and isinstance(oracle_metrics[name], float)
    }
    return {"oracle": oracle_metrics, "tax": tax}
