"""Index builder — offline (teacher-path) indexing pass.

Reads the index config (embedding space/dims and storage backends), runs the
default MuJoCo world to produce observation atoms, and ingests them into a
retrieval engine built on the CONFIGURED backends — i.e. with
``configs/index/default.yaml`` this genuinely exercises LanceDB (vector) and
DuckDB (timeseries). In live mode the embedder is the Gemini teacher
(authoritative offline indexing per CLAUDE.md §5.3); in mock mode it is the
deterministic local embedder, so the command works offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mws.core.config import get_settings
from mws.core.logging import get_logger
from mws.core.types import CloudMode
from mws.embedding.factory import create_embedder
from mws.retrieval.engine import RetrievalEngine
from mws.sim.sensors import extract_observation_atoms
from mws.sim.world import MuJoCoWorld
from mws.storage.registry import StoreRegistry

logger = get_logger(__name__)


def _load_index_config(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        logger.warning("Index config not found; using defaults", path=config_path)
        return {}
    with open(path) as f:
        return (yaml.safe_load(f) or {}).get("index", {})


def build_index(config_path: str, seed: int = 0, steps: int = 100) -> dict[str, Any]:
    """Build the multi-index from freshly generated sim atoms.

    Returns a summary dict (atom/vector/timeseries counts and backends) so
    callers/tests can verify the pass actually indexed something.
    """
    settings = get_settings()
    index_cfg = _load_index_config(config_path)
    vector_backend = str(index_cfg.get("vector_backend", "memory"))
    timeseries_backend = str(index_cfg.get("timeseries_backend", "memory"))
    # LanceDB is a disk format; map the config name onto the registry switch.
    vector_backend = "lancedb" if vector_backend == "lancedb" else "memory"
    timeseries_backend = "duckdb" if timeseries_backend == "duckdb" else "memory"

    emb_role = "teacher" if settings.cloud_mode == CloudMode.LIVE else "student"
    embedder = create_embedder(settings, role=emb_role)
    # Space/dims come from the embedder (single source of truth — see
    # BaseScenario._init_run).
    stores = StoreRegistry(
        embedding_space=embedder.space,
        embedding_dims=embedder.dims,
        vector_backend=vector_backend,
        timeseries_backend=timeseries_backend,
    )
    engine = RetrievalEngine(stores=stores, embedder=embedder)

    # Source atoms: step the default warehouse world and extract observations.
    world = MuJoCoWorld(xml_path="configs/worlds/warehouse.xml", seed=seed)
    world.step(steps)
    atoms = extract_observation_atoms(world, seed=seed)
    for atom in atoms:
        engine.ingest(atom)

    summary = {
        "config": config_path,
        "embedding_space": str(embedder.space),
        "vector_backend": vector_backend,
        "timeseries_backend": timeseries_backend,
        "n_atoms": engine.atom_count,
        "vector_size": stores.vector.size,
        "timeseries_size": stores.timeseries.size,
        "seed": seed,
    }
    logger.info("Index build complete", **summary)
    return summary


# --- Async Batch API path (IMPROVEMENT E3, CLAUDE.md §5.3) ---
#
# Offline authoritative indexing via the Gemini Batch API: 50% of standard
# embedding cost, asynchronous (24h target turnaround), resumable. The
# interactive path (scenario ingestion) stays on synchronous batched requests
# (E2) — the Batch API is for offline re-indexing only.

_TERMINAL_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
    "JOB_STATE_PARTIALLY_SUCCEEDED",
}
#: Batch API price factor vs standard embedding pricing (official docs).
_BATCH_DISCOUNT = 0.5


def submit_embedding_batch(client: Any, model: str, texts: list[str], dims: int) -> Any:
    """Submit ONE Batch API embedding job for the given (final) texts."""
    from google.genai import types

    src = types.EmbeddingsBatchJobSource(
        inlined_requests=types.EmbedContentBatch(
            contents=[types.Content(parts=[types.Part.from_text(text=t)]) for t in texts],
            config=types.EmbedContentConfig(output_dimensionality=dims),
        )
    )
    job = client.batches.create_embeddings(
        model=model,
        src=src,
        config={"display_name": "mws-index-build"},
    )
    logger.info("Batch embedding job submitted", job_name=job.name, n_texts=len(texts))
    return job


def poll_batch_job(
    client: Any,
    job_name: str,
    poll_interval: float = 15.0,
    timeout: float = 600.0,
) -> Any:
    """Poll a batch job until a terminal state or timeout (resumable).

    Returns the last-seen job object; callers check ``job.state``. A timeout
    is NOT an error — re-invoke with --resume-job to continue (CLAUDE.md §3:
    long jobs must be interruptible/resumable).
    """
    import time as _time

    deadline = _time.monotonic() + timeout
    while True:
        job = client.batches.get(name=job_name)
        state = str(job.state)
        if any(t in state for t in _TERMINAL_STATES):
            logger.info("Batch job terminal", job_name=job_name, state=state)
            return job
        if _time.monotonic() >= deadline:
            logger.info(
                "Batch job still running at timeout (resume with --resume-job)",
                job_name=job_name,
                state=state,
            )
            return job
        _time.sleep(poll_interval)


def extract_batch_vectors(job: Any, n_expected: int) -> tuple[list[list[float]], int]:
    """Extract per-text vectors and MEASURED total tokens from a finished job."""
    responses = getattr(job.dest, "inlined_embed_content_responses", None) or []
    if len(responses) != n_expected:
        raise RuntimeError(f"Batch job returned {len(responses)} responses for {n_expected} texts")
    vectors: list[list[float]] = []
    measured_tokens = 0
    for i, item in enumerate(responses):
        response = item.response
        embedding = response.embedding if response is not None else None
        if embedding is None or embedding.values is None:
            raise RuntimeError(f"Batch job item {i} has no embedding: {item.error}")
        vectors.append(list(embedding.values))
        measured_tokens += int(response.token_count or 0) if response is not None else 0
    return vectors, measured_tokens


def build_index_batch_api(
    config_path: str,
    seed: int = 0,
    steps: int = 100,
    resume_job_name: str | None = None,
    poll_interval: float = 15.0,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Build the index with embeddings from the async Batch API (live only).

    Atoms are generated deterministically from the seed, so a resumed
    invocation reconstructs the same texts and attaches the job's vectors to
    the same atoms. The job name, scale, and measured cost are recorded in a
    runs/ manifest (CLAUDE.md §5.5).
    """
    import uuid

    from mws.eval.report import create_manifest, save_report

    settings = get_settings()
    if settings.cloud_mode != CloudMode.LIVE or not settings.google_api_key:
        raise ValueError(
            "--batch-api requires MWS_CLOUD_MODE=live and GOOGLE_API_KEY "
            "(the Batch API is a real cloud job; use the default sync path in mock mode)."
        )

    from mws.embedding.teacher import GeminiTeacherEmbedder

    teacher = GeminiTeacherEmbedder(api_key=settings.google_api_key)

    # Deterministic source atoms (same as the sync path).
    world = MuJoCoWorld(xml_path="configs/worlds/warehouse.xml", seed=seed)
    world.step(steps)
    atoms = extract_observation_atoms(world, seed=seed)
    doc_atoms = [a for a in atoms if a.text_summary]
    final_texts = [teacher.format_document(a.text_summary) for a in doc_atoms]

    if resume_job_name:
        job_name = resume_job_name
    else:
        job = submit_embedding_batch(teacher._client, teacher._model, final_texts, teacher.dims)
        job_name = str(job.name)

    job = poll_batch_job(teacher._client, job_name, poll_interval=poll_interval, timeout=timeout)
    state = str(job.state)
    run_id = f"index_build_batch-{seed}-{uuid.uuid4().hex[:8]}"

    summary: dict[str, Any] = {
        "config": config_path,
        "mode": "batch_api",
        "batch_job_name": job_name,
        "batch_job_state": state,
        "n_texts": len(final_texts),
        "seed": seed,
        "run_id": run_id,
    }

    if "JOB_STATE_SUCCEEDED" not in state:
        # Not done (or failed): record what we know; resumable via --resume-job.
        summary["resumable"] = "JOB_STATE_FAILED" not in state
        manifest = create_manifest(run_id=run_id, scenario="index_build_batch", seed=seed)
        save_report(run_id=run_id, manifest=manifest, metrics=summary)
        logger.info("Batch index build not complete", **summary)
        return summary

    vectors, measured_tokens = extract_batch_vectors(job, len(final_texts))

    # Attach vectors, then ingest — the engine's reuse path indexes them with
    # ZERO additional synchronous embedding calls.
    for atom, text, vector in zip(doc_atoms, final_texts, vectors, strict=True):
        atom.add_embedding(teacher.space, vector, teacher._content_hash(text))

    index_cfg = _load_index_config(config_path)
    vector_backend = (
        "lancedb" if str(index_cfg.get("vector_backend", "memory")) == "lancedb" else "memory"
    )
    timeseries_backend = (
        "duckdb" if str(index_cfg.get("timeseries_backend", "memory")) == "duckdb" else "memory"
    )
    stores = StoreRegistry(
        embedding_space=teacher.space,
        embedding_dims=teacher.dims,
        vector_backend=vector_backend,
        timeseries_backend=timeseries_backend,
    )
    engine = RetrievalEngine(stores=stores, embedder=teacher)
    for atom in atoms:
        engine.ingest(atom)

    # Cost: Batch API bills 50% of standard. Token counts are taken from the
    # job's per-item token_count when the API returns them; observed live runs
    # return None there, so we fall back to the chars/4 estimate and say so
    # (tokens_source is machine-readable — never report an estimate as measured).
    if measured_tokens > 0:
        tokens, tokens_source = measured_tokens, "measured"
    else:
        tokens = sum(max(1, len(t) // 4) for t in final_texts)
        tokens_source = "estimated"
    est_cost = tokens / 1000 * 0.00004 * _BATCH_DISCOUNT
    summary.update(
        {
            "embedding_space": str(teacher.space),
            "vector_backend": vector_backend,
            "timeseries_backend": timeseries_backend,
            "n_atoms": engine.atom_count,
            "vector_size": stores.vector.size,
            "tokens": tokens,
            "tokens_source": tokens_source,
            "estimated_cost_usd": round(est_cost, 8),
            "sync_embedding_requests_during_ingest": engine._cost.embedding_requests,
        }
    )
    manifest = create_manifest(run_id=run_id, scenario="index_build_batch", seed=seed)
    save_report(run_id=run_id, manifest=manifest, metrics=summary)
    logger.info("Batch index build complete", **summary)

    # Sanity: ingest must have reused every attached embedding.
    if engine._cost.embedding_requests:
        logger.warning(
            "Batch index build made sync embedding calls (reuse path miss)",
            n=engine._cost.embedding_requests,
        )
    return summary
