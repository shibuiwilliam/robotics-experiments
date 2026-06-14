"""Metrics report generation and run manifest."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from mws.core.config import get_settings
from mws.core.logging import get_logger

logger = get_logger(__name__)


def get_git_sha() -> str:
    """Get current git SHA, or 'unknown' if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()[:12]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


#: Cap on how many dirty paths the manifest records (IMPROVEMENT M20). The
#: git_dirty flag already signals dirtiness; this list is for diagnosis, so a
#: bounded sample is enough — a truncation marker is appended if exceeded.
_MAX_DIRTY_PATHS = 20


def get_git_state() -> dict[str, Any]:
    """Working-tree state for the CURRENT directory subtree (IMPROVEMENT M15/M20).

    A SHA alone is misleading when the tree is dirty — or worse, entirely
    untracked (then the SHA describes none of the code that ran). The dirty
    flag, the untracked-tree flag, AND the first ``_MAX_DIRTY_PATHS`` dirty
    paths are recorded machine-readably so a manifest alone explains a
    ``git_dirty=true`` without re-running ``git status``.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", "."],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"git_dirty": None, "git_untracked_tree": None, "git_dirty_paths": None}
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    untracked_tree = any(
        line.startswith("??") and line.split(maxsplit=1)[1] in ("./", ".") for line in lines
    )
    dirty_paths = lines[:_MAX_DIRTY_PATHS]
    if len(lines) > _MAX_DIRTY_PATHS:
        dirty_paths = [*dirty_paths, f"... (+{len(lines) - _MAX_DIRTY_PATHS} more)"]
    return {
        "git_dirty": bool(lines),
        "git_untracked_tree": untracked_tree,
        "git_dirty_paths": dirty_paths,
    }


def _model_ids() -> dict[str, str]:
    """Model identifiers from their single sources of truth (no duplicated strings)."""
    from mws.agents.live import GEMINI_LLM_MODEL
    from mws.embedding.teacher import GEMINI_EMBEDDING_MODEL

    return {
        "embedding": GEMINI_EMBEDDING_MODEL,
        "llm": GEMINI_LLM_MODEL,
    }


_KEY_DEPENDENCIES = ("mujoco", "lancedb", "duckdb", "google-genai", "sentence-transformers")


def _dependency_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    versions: dict[str, str] = {}
    for name in _KEY_DEPENDENCIES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def _vector_backend_info(
    settings: Any, embedding_space: Any, embedding_dims: int
) -> dict[str, Any]:
    """Which vector store produced this run (IMPROVEMENT M21/M22).

    The backend now varies per run (Elasticsearch default for scenario runs,
    in-memory for the pytest suite, LanceDB optional), so the manifest must say
    which one ran. For Elasticsearch the URL and the index NAMING CONVENTION
    are recorded — not a concrete index name, because each store instance gets
    a unique per-run uuid-suffixed index that is dropped on teardown, so a
    single name would be both stale and incomplete. The ``*`` pattern matches
    the run's (many, ephemeral) indices in the cluster.

    The index pattern is built from the SAME space/dims the manifest reports
    (the embedder's, when known — M22), so embedding_space and the index can
    never disagree, even in live mode where the embedder's space differs from
    settings.default_embedding_space.
    """
    info: dict[str, Any] = {"vector_backend": settings.vector_backend}
    if settings.vector_backend == "elasticsearch":
        from mws.storage.vector import DEFAULT_ES_INDEX_PREFIX, _es_index_name

        base = _es_index_name(DEFAULT_ES_INDEX_PREFIX, embedding_space, embedding_dims)
        info["elasticsearch_url"] = settings.elasticsearch_url
        info["elasticsearch_index"] = f"{base}-*"  # per-run uuid-suffixed
    return info


def create_manifest(
    run_id: str,
    scenario: str,
    seed: int,
    extra: dict[str, Any] | None = None,
    embedder: Any | None = None,
) -> dict[str, Any]:
    """Create a run manifest with config, git state, deps, models, and settings.

    CLAUDE.md §9: a manifest must identify the code (SHA + dirty/untracked
    flags + dirty paths), the models, the embedding space, the vector backend,
    the seed, the cloud mode, and the settings that shape measurements (batch
    size) — sufficient to reproduce.

    The embedding space/dims (and the ES index pattern derived from them) come
    from the run's actual ``embedder`` when supplied — the single source of
    truth that the vector store and ES index are namespaced by (CLAUDE.md §6).
    Without an embedder, they fall back to ``settings.default_embedding_space``
    / ``settings.embedding_dims``. Passing the embedder avoids the live-mode
    drift where the embedder uses gemini2-768-v2 but a stale env makes settings
    report a different space (IMPROVEMENT M22).
    """
    settings = get_settings()
    if embedder is not None:
        embedding_space: Any = embedder.space
        embedding_dims = embedder.dims
    else:
        embedding_space = settings.default_embedding_space
        embedding_dims = settings.embedding_dims
    manifest = {
        "run_id": run_id,
        "scenario": scenario,
        "timestamp": datetime.now().isoformat(),
        "git_sha": get_git_sha(),
        **get_git_state(),
        "seed": seed,
        "cloud_mode": settings.cloud_mode,
        "embedding_space": embedding_space,
        "embedding_dims": embedding_dims,
        "embedding_batch_size": settings.embedding_batch_size,
        **_vector_backend_info(settings, embedding_space, embedding_dims),
        "model_ids": _model_ids(),
        "python_version": _python_version(),
        "dependency_versions": _dependency_versions(),
    }
    if extra:
        manifest.update(extra)
    return manifest


def _python_version() -> str:
    import sys

    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def save_report(
    run_id: str,
    manifest: dict[str, Any],
    metrics: dict[str, Any],
    audit_log: list[dict[str, Any]] | None = None,
) -> Path:
    """Save a metrics report and manifest to runs/<run_id>/."""
    settings = get_settings()
    run_dir = settings.run_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Save manifest
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

    # Save metrics
    metrics_path = run_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, default=str))

    # Save audit log
    if audit_log:
        audit_path = run_dir / "audit_log.json"
        audit_path.write_text(json.dumps(audit_log, indent=2, default=str))

    logger.info("Report saved", run_dir=str(run_dir))
    return run_dir


def generate_report(run_id: str) -> None:
    """Generate/display a report for an existing run."""
    settings = get_settings()
    run_dir = settings.run_dir / run_id

    if not run_dir.exists():
        logger.error("Run directory not found", run_id=run_id)
        return

    manifest_path = run_dir / "manifest.json"
    metrics_path = run_dir / "metrics.json"

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        logger.info("=== Run Manifest ===")
        for k, v in manifest.items():
            logger.info(f"  {k}: {v}")

    if metrics_path.exists():
        metrics = json.loads(metrics_path.read_text())
        logger.info("=== Metrics ===")
        for k, v in metrics.items():
            logger.info(f"  {k}: {v}")
