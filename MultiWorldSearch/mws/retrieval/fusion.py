"""Score fusion for multi-index retrieval — Reciprocal Rank Fusion (RRF)."""

from __future__ import annotations

from mws.retrieval.query import RetrievalResult


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[tuple[str, float]]],
    weights: dict[str, float] | None = None,
    k: int = 60,
    top_n: int = 10,
) -> list[RetrievalResult]:
    """Fuse multiple ranked lists using weighted RRF.

    Args:
        ranked_lists: {index_name: [(atom_id, score), ...]} sorted by score desc.
        weights: {index_name: weight}. Default: equal weights. When an explicit
            weights dict is given, an index missing from it is EXCLUDED
            (weight 0.0) — never silently given a default weight, which would
            let an unlisted index dominate the fusion.
        k: RRF constant (higher = smoother).
        top_n: Number of results to return.

    Returns:
        Fused results sorted by combined score descending.
    """
    if weights is None:
        weights = {name: 1.0 for name in ranked_lists}

    # Accumulate RRF scores per atom
    rrf_scores: dict[str, float] = {}
    per_index_scores: dict[str, dict[str, float]] = {}

    for index_name, ranked in ranked_lists.items():
        w = weights.get(index_name, 0.0)
        if w == 0.0:
            continue
        for rank, (atom_id, score) in enumerate(ranked):
            rrf_score = w / (k + rank + 1)
            rrf_scores[atom_id] = rrf_scores.get(atom_id, 0.0) + rrf_score
            if atom_id not in per_index_scores:
                per_index_scores[atom_id] = {}
            per_index_scores[atom_id][index_name] = score

    # Sort by fused score
    sorted_items = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

    results = []
    for atom_id, fused_score in sorted_items[:top_n]:
        results.append(
            RetrievalResult(
                atom_id=atom_id,
                score=fused_score,
                scores_by_index=per_index_scores.get(atom_id, {}),
            )
        )
    return results
