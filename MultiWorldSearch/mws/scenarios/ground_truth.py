"""Ground-truth relevance label generation from sim + business data.

Uses the simulation privilege (PROJECT.md §5.3): MuJoCo ground truth + business
data links provide entity IDs and attributes, so relevance labels can be
auto-derived without human annotation.
"""

from __future__ import annotations

from mws.core.atom import ExperienceAtom
from mws.core.logging import get_logger
from mws.eval.metrics import mrr, ndcg_at_k, recall_at_k

logger = get_logger(__name__)


def derive_relevance(
    query_entity_ids: list[str],
    query_tags: list[str],
    atoms: list[ExperienceAtom],
    tag_threshold: int = 2,
) -> set[str]:
    """Auto-derive relevant atom IDs from ground-truth entity links and tags.

    An atom is relevant if:
    - Its entity_id matches any of the query entity IDs, OR
    - It shares >= tag_threshold tags with the query tags.

    Args:
        query_entity_ids: Ground-truth entity IDs that the query is about.
        query_tags: Tags characterizing the query intent.
        atoms: All atoms in the store.
        tag_threshold: Minimum tag overlap for tag-based relevance.
    """
    relevant = set()
    query_tag_set = set(query_tags)
    entity_set = set(query_entity_ids)

    for atom in atoms:
        if atom.entity_id and atom.entity_id in entity_set:
            relevant.add(atom.atom_id)
            continue
        if len(query_tag_set & set(atom.tags)) >= tag_threshold:
            relevant.add(atom.atom_id)

    return relevant


def compute_retrieval_metrics(
    retrieved_ids: list[str],
    relevant_ids: set[str],
    ks: list[int] | None = None,
) -> dict[str, float]:
    """Compute standard retrieval metrics from retrieved vs relevant sets.

    Returns dict with recall_at_k, mrr, ndcg_at_k for each k.
    """
    if ks is None:
        ks = [5, 10]
    metrics: dict[str, float] = {
        "mrr": mrr(retrieved_ids, relevant_ids),
        "n_relevant": float(len(relevant_ids)),
        "n_retrieved": float(len(retrieved_ids)),
    }
    for k in ks:
        metrics[f"recall_at_{k}"] = recall_at_k(retrieved_ids, relevant_ids, k=k)
        metrics[f"ndcg_at_{k}"] = ndcg_at_k(retrieved_ids, relevant_ids, k=k)
    return metrics
