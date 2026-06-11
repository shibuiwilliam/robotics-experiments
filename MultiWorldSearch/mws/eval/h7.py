"""H7 teacher/student embedding A/B — latency vs retrieval quality.

PROJECT.md §6-2 / H7: the authoritative cloud teacher (Gemini Embedding 2)
indexes offline; a local student serves the hot path. This harness measures,
on the SAME corpus and queries, each tier's per-call embedding latency
(p50/p95) and retrieval quality (Recall@k / MRR), and renders a verdict
against a PRE-REGISTERED criterion (declared before running, PROJECT.md §10.2):

    H7 is SUPPORTED iff
        student_p50_latency <= teacher_p50_latency / 5     (hot-path speedup)
        AND student_recall_at_5 >= 0.8 * teacher_recall_at_5  (quality floor)

Either outcome (supported / not supported) is a valid experimental result.
Each tier gets its own VectorStore tagged with its own embedding space —
spaces are never mixed (CLAUDE.md §6).
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from mws.core.config import MWSSettings
from mws.core.logging import get_logger
from mws.eval.metrics import mrr, recall_at_k
from mws.storage.vector import VectorStore

logger = get_logger(__name__)

# Pre-registered H7 criterion (do not tune after seeing results).
SPEEDUP_FLOOR = 5.0
QUALITY_FLOOR = 0.8

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


def _measure_tier(embedder: Any, label: str) -> dict[str, Any]:
    """Embed corpus + queries with one tier, measure latency and retrieval."""
    store = VectorStore(embedding_space=embedder.space, dims=embedder.dims)
    latencies_ms: list[float] = []
    doc_ids: list[str] = []

    for i, (_topic, text) in enumerate(_CORPUS):
        start = time.perf_counter()
        # Document side of the asymmetric scheme (E1); identity for the student.
        result = embedder.embed_document(text)
        latencies_ms.append((time.perf_counter() - start) * 1000)
        doc_id = f"doc_{i:02d}"
        store.add(doc_id, np.array(result.vector, dtype=np.float32))
        doc_ids.append(doc_id)

    recalls_5: list[float] = []
    recalls_10: list[float] = []
    mrrs: list[float] = []
    for topic, query_text in _QUERIES:
        start = time.perf_counter()
        q = embedder.embed_query(query_text)
        latencies_ms.append((time.perf_counter() - start) * 1000)
        hits = [doc_id for doc_id, _score in store.search(q.to_numpy(), top_k=10)]
        relevant = {f"doc_{i:02d}" for i, (t, _) in enumerate(_CORPUS) if t == topic}
        recalls_5.append(recall_at_k(hits, relevant, k=5))
        recalls_10.append(recall_at_k(hits, relevant, k=10))
        mrrs.append(mrr(hits, relevant))

    s = sorted(latencies_ms)
    n = len(s)
    return {
        "tier": label,
        "model": getattr(embedder, "_model_name", type(embedder).__name__),
        "space": str(embedder.space),
        "dims": embedder.dims,
        "is_stub": bool(getattr(embedder, "is_stub", False)),
        "n_embed_calls": n,
        "embed_p50_ms": round(s[n // 2], 3),
        "embed_p95_ms": round(s[int(n * 0.95)], 3),
        "recall_at_5": round(float(np.mean(recalls_5)), 4),
        "recall_at_10": round(float(np.mean(recalls_10)), 4),
        "mrr": round(float(np.mean(mrrs)), 4),
    }


def run_h7_ab(settings: MWSSettings | None = None) -> dict[str, Any]:
    """Run the teacher-vs-student A/B and render the pre-registered verdict.

    The student tier needs the ``student`` extra; the teacher tier needs
    GOOGLE_API_KEY. A missing tier is reported as unavailable and the verdict
    becomes "undetermined" (never silently passed).
    """
    if settings is None:
        from mws.core.config import get_settings

        settings = get_settings()

    out: dict[str, Any] = {
        "criterion": {
            "speedup_floor": SPEEDUP_FLOOR,
            "quality_floor": QUALITY_FLOOR,
            "definition": (
                "supported iff student_p50 <= teacher_p50/speedup_floor "
                "AND student_recall_at_5 >= quality_floor * teacher_recall_at_5"
            ),
        },
        "corpus_size": len(_CORPUS),
        "n_queries": len(_QUERIES),
    }

    # Student tier (local, real model required for a meaningful verdict)
    try:
        from mws.embedding.student import LocalStudentEmbedder

        student = LocalStudentEmbedder(model_name=settings.student_model)
        out["student"] = _measure_tier(student, "student")
    except ImportError as exc:
        out["student"] = {"unavailable": str(exc)}

    # Teacher tier (cloud)
    if settings.google_api_key:
        from mws.core.types import EmbeddingSpace
        from mws.embedding.teacher import GeminiTeacherEmbedder

        teacher = GeminiTeacherEmbedder(
            api_key=settings.google_api_key,
            space=EmbeddingSpace.GEMINI_768_V2,
            dims=768,
        )
        out["teacher"] = _measure_tier(teacher, "teacher")
    else:
        out["teacher"] = {"unavailable": "GOOGLE_API_KEY not set"}

    # Verdict against the pre-registered criterion
    s, t = out["student"], out["teacher"]
    if "unavailable" in s or "unavailable" in t or s.get("is_stub"):
        out["verdict"] = {
            "h7_supported": None,
            "reason": "undetermined: both a REAL student and the live teacher are required",
        }
        return out

    s_p50, t_p50 = float(s["embed_p50_ms"]), float(t["embed_p50_ms"])
    s_r5, t_r5 = float(s["recall_at_5"]), float(t["recall_at_5"])
    speedup = t_p50 / s_p50 if s_p50 > 0 else float("inf")
    quality_ratio = s_r5 / t_r5 if t_r5 > 0 else float("inf")
    supported = speedup >= SPEEDUP_FLOOR and quality_ratio >= QUALITY_FLOOR
    out["verdict"] = {
        "h7_supported": supported,
        "latency_speedup": round(speedup, 2),
        "quality_ratio": round(quality_ratio, 4),
        "reason": (
            f"student p50 {s['embed_p50_ms']}ms vs teacher {t['embed_p50_ms']}ms "
            f"(speedup {speedup:.1f}x, floor {SPEEDUP_FLOOR}x); "
            f"recall@5 {s['recall_at_5']} vs {t['recall_at_5']} "
            f"(ratio {quality_ratio:.2f}, floor {QUALITY_FLOOR})"
        ),
    }
    return out
