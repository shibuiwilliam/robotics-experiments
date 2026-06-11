"""E1 A/B — task-instruction prefixes (v2) vs raw embedding (v1).

IMPROVEMENT P9/E1: gemini-embedding-2 expresses retrieval asymmetry with
prompt prefixes (query: ``task: search result | query: ...``; document:
``title: none | text: ...``) instead of a task_type parameter. This harness
measures, on the SAME corpus and queries (the deterministic H7 set), the
retrieval quality of the prefixed scheme (space v2) against the raw scheme
(space v1), with a PRE-REGISTERED criterion declared before running
(PROJECT.md §10.2):

    metrics: Recall@5 / Recall@10 / MRR (averaged over the query set)
    hypothesis: prefixed (v2) Recall@5 >= raw (v1) Recall@5

Either outcome is a valid experimental result and is reported honestly.
Each arm gets its own VectorStore tagged with its own embedding space —
v1 and v2 vectors are never mixed (CLAUDE.md §6).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from mws.core.config import MWSSettings
from mws.core.logging import get_logger
from mws.core.types import EmbeddingSpace
from mws.eval.h7 import _CORPUS, _QUERIES
from mws.eval.metrics import mrr, recall_at_k
from mws.storage.vector import VectorStore

logger = get_logger(__name__)

# Pre-registered E1 criterion (declared before running; do not tune after).
PRE_REGISTERED = {
    "metrics": ["recall_at_5", "recall_at_10", "mrr"],
    "hypothesis": "prefixed(v2) recall_at_5 >= raw(v1) recall_at_5",
}


def _measure_arm(embedder: Any, label: str, *, prefixed: bool) -> dict[str, Any]:
    """Index the corpus and run the queries with one arm's scheme."""
    store = VectorStore(embedding_space=embedder.space, dims=embedder.dims)

    for i, (_topic, text) in enumerate(_CORPUS):
        result = embedder.embed_document(text) if prefixed else embedder.embed_text(text)
        store.add(f"doc_{i:02d}", np.array(result.vector, dtype=np.float32))

    recalls_5: list[float] = []
    recalls_10: list[float] = []
    mrrs: list[float] = []
    for topic, query_text in _QUERIES:
        q = embedder.embed_query(query_text) if prefixed else embedder.embed_text(query_text)
        hits = [doc_id for doc_id, _score in store.search(q.to_numpy(), top_k=10)]
        relevant = {f"doc_{i:02d}" for i, (t, _) in enumerate(_CORPUS) if t == topic}
        recalls_5.append(recall_at_k(hits, relevant, k=5))
        recalls_10.append(recall_at_k(hits, relevant, k=10))
        mrrs.append(mrr(hits, relevant))

    return {
        "arm": label,
        "space": str(embedder.space),
        "prefixed": prefixed,
        "recall_at_5": round(float(np.mean(recalls_5)), 4),
        "recall_at_10": round(float(np.mean(recalls_10)), 4),
        "mrr": round(float(np.mean(mrrs)), 4),
    }


def run_e1_ab(settings: MWSSettings | None = None) -> dict[str, Any]:
    """Run the prefixed-vs-raw A/B against the live teacher.

    Requires GOOGLE_API_KEY (this is a live experiment by definition: the
    prefixes only exist on the Gemini teacher; mock embedders are symmetric).
    Cost: 2 arms x (20 docs + 6 queries) = 52 embedding requests.
    """
    if settings is None:
        from mws.core.config import get_settings

        settings = get_settings()

    out: dict[str, Any] = {
        "pre_registered": PRE_REGISTERED,
        "corpus_size": len(_CORPUS),
        "n_queries": len(_QUERIES),
    }

    if not settings.google_api_key:
        out["verdict"] = {"prefix_improves": None, "reason": "GOOGLE_API_KEY not set"}
        return out

    from mws.embedding.teacher import GeminiTeacherEmbedder

    raw_arm = GeminiTeacherEmbedder(
        api_key=settings.google_api_key,
        space=EmbeddingSpace.GEMINI_768,  # v1: raw scheme
        dims=768,
    )
    prefixed_arm = GeminiTeacherEmbedder(
        api_key=settings.google_api_key,
        space=EmbeddingSpace.GEMINI_768_V2,  # v2: prefixed scheme
        dims=768,
    )

    out["raw"] = _measure_arm(raw_arm, "raw_v1", prefixed=False)
    out["prefixed"] = _measure_arm(prefixed_arm, "prefixed_v2", prefixed=True)

    r5_raw = float(out["raw"]["recall_at_5"])
    r5_pre = float(out["prefixed"]["recall_at_5"])
    out["verdict"] = {
        "prefix_improves": r5_pre >= r5_raw,
        "delta_recall_at_5": round(r5_pre - r5_raw, 4),
        "delta_recall_at_10": round(
            float(out["prefixed"]["recall_at_10"]) - float(out["raw"]["recall_at_10"]), 4
        ),
        "delta_mrr": round(float(out["prefixed"]["mrr"]) - float(out["raw"]["mrr"]), 4),
        "reason": (f"prefixed recall@5 {r5_pre} vs raw {r5_raw} (pre-registered: prefixed >= raw)"),
    }
    return out
