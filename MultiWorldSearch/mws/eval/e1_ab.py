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
from mws.eval.metrics import mrr, recall_at_k
from mws.storage.vector import VectorStore

# Deterministic corpus: 4 entities x 3 documents + 8 noise documents.
_CORPUS: list[tuple[str, str]] = [
    ("pump", "Pump_07 bearing vibration exceeded threshold, replacement scheduled."),
    ("pump", "Work order WO-101: pump_07 bearing replaced, torque spec 45 Nm applied."),
    ("pump", "SOP-PUMP-07 step 4: inspect pump bearing runout, limit 0.05 mm."),
    ("valve", "Valve_03 requires counter-clockwise loosening per maintenance skill demo."),
    ("valve", "Skill demonstration: loosen valve_03 handle with two-finger grip."),
    ("valve", "Valve_03 pressure rating 100 PSI, do not operate above the limit."),
    ("forklift", "Forklift_12 battery charge at 18 percent, return to charging bay."),
    ("forklift", "Asset registry: forklift_12 assigned to maintenance bay this week."),
    ("forklift", "Forklift_12 observed on warehouse floor, status mismatch with registry."),
    ("sds", "SDS substance_X: toxic vapor, requires PPE level C and ventilation."),
    ("sds", "Chemical leak response: evacuate via south exit per substance_X SDS."),
    ("sds", "Substance_X storage requires sealed containers below 25 degrees."),
    ("noise", "Cafeteria menu for Friday includes soup and seasonal vegetables."),
    ("noise", "Quarterly all-hands meeting moved to the second floor auditorium."),
    ("noise", "Parking lot B is closed for resurfacing until next Monday."),
    ("noise", "New visitor badge printer installed at the north reception desk."),
    ("noise", "The annual safety poster contest accepts entries until month end."),
    ("noise", "Window cleaning is scheduled for building C this weekend."),
    ("noise", "The vending machine on floor two now accepts contactless payment."),
    ("noise", "Office plants are watered by the facilities team every Tuesday."),
]

# Paraphrase queries (do not share exact wording with corpus docs).
_QUERIES: list[tuple[str, str]] = [
    ("pump", "how do I check the pump bearing wear and what torque to use"),
    ("pump", "vibration problem on the pump, which work order fixed it"),
    ("valve", "what is the technique to undo the valve handle"),
    ("valve", "maximum safe pressure before operating the valve"),
    ("forklift", "where should the low battery forklift go"),
    ("sds", "what protective equipment for the toxic chemical spill"),
]

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
