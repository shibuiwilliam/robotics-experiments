"""Live Gemini Embedding 2 tests (IMPROVEMENT P9 acceptance, @pytest.mark.live).

Run with: uv run pytest -m live tests/embedding/test_teacher_live.py
Cost: a handful of embedding requests (+1 tiny Batch API job).
"""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

from mws.embedding.teacher import GeminiTeacherEmbedder

pytestmark = pytest.mark.live


def _teacher() -> GeminiTeacherEmbedder:
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        pytest.skip("GOOGLE_API_KEY not set")
    return GeminiTeacherEmbedder(api_key=key)


def test_truncated_dims_are_auto_normalized() -> None:
    """E4: official docs — gemini-embedding-2 auto-normalizes truncated dims."""
    teacher = _teacher()
    result = teacher.embed_text("normalization check for the 768-dim output")
    assert result.dims == 768
    norm = float(np.linalg.norm(np.array(result.vector, dtype=np.float64)))
    assert abs(norm - 1.0) < 1e-3, f"expected unit norm, got {norm}"


def test_batch_request_matches_single_requests() -> None:
    """E2: one multi-Content request returns the same vectors as singles."""
    teacher = _teacher()
    texts = ["pump bearing inspection", "valve pressure limit", "forklift battery status"]
    batch = teacher.embed_batch(texts)
    singles = [teacher.embed_text(t) for t in texts]
    assert len(batch) == 3
    for b, s in zip(batch, singles, strict=True):
        cos = float(
            np.dot(np.array(b.vector), np.array(s.vector))
            / (np.linalg.norm(b.vector) * np.linalg.norm(s.vector))
        )
        assert cos > 0.999, f"batch/single divergence: cos={cos}"


def test_document_and_query_prefixes_change_the_vector() -> None:
    """E1: the asymmetric scheme actually produces different coordinates."""
    teacher = _teacher()
    raw = teacher.embed_text("valve_03 manual")
    doc = teacher.embed_document("valve_03 manual")
    query = teacher.embed_query("valve_03 manual")
    for a, b in ((raw, doc), (raw, query), (doc, query)):
        cos = float(
            np.dot(np.array(a.vector), np.array(b.vector))
            / (np.linalg.norm(a.vector) * np.linalg.norm(b.vector))
        )
        assert cos < 0.9999, "prefix had no effect on the embedding"


def test_batch_api_job_end_to_end_or_skip() -> None:
    """E3: a tiny real Batch API job; vectors match the sync path.

    Batch turnaround is best-effort (24h target) — if the job has not
    finished within the polling budget we SKIP honestly rather than fail.
    """
    from mws.scenarios.index_builder import (
        extract_batch_vectors,
        poll_batch_job,
        submit_embedding_batch,
    )

    teacher = _teacher()
    texts = [teacher.format_document(t) for t in ("alpha doc", "beta doc", "gamma doc")]
    job = submit_embedding_batch(teacher._client, teacher._model, texts, teacher.dims)

    deadline = time.monotonic() + 300
    job = poll_batch_job(
        teacher._client,
        str(job.name),
        poll_interval=10.0,
        timeout=max(0.0, deadline - time.monotonic()),
    )
    if "JOB_STATE_SUCCEEDED" not in str(job.state):
        pytest.skip(f"batch job not finished in budget (state={job.state}, name={job.name})")

    vectors, measured_tokens = extract_batch_vectors(job, n_expected=3)
    # token_count is best-effort: live jobs have been observed to return None
    # (the build falls back to a labeled estimate); only the vectors matter here.
    assert measured_tokens >= 0
    sync = [teacher.embed_text(t) for t in texts]
    for batch_vec, sync_res in zip(vectors, sync, strict=True):
        cos = float(
            np.dot(np.array(batch_vec), np.array(sync_res.vector))
            / (np.linalg.norm(batch_vec) * np.linalg.norm(sync_res.vector))
        )
        assert cos > 0.999, f"batch-API/sync divergence: cos={cos}"
