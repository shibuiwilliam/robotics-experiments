"""Run manifest generation — records everything needed to reproduce a run.

Each run produces a manifest.json with config, git commit, seed,
dependency versions, and metrics. This is the traceability chain
from code to results (CLAUDE.md §10).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def get_git_commit() -> str:
    """Get current git commit hash, or 'unknown' if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except FileNotFoundError:
        return "unknown"


def get_vla_info(encoder: object | None = None) -> dict[str, str]:
    """Get VLA encoder mode and model version.

    Args:
        encoder: Optional VLAEncoder instance. If provided, reports the
            actual runtime mode instead of guessing from import availability.
    """
    if encoder is not None and hasattr(encoder, "_use_real_clip"):
        if encoder._use_real_clip:
            return {"mode": "clip", "model_version": "ViT-B-32/laion2b_s34b_b79k"}
        return {"mode": "hash_fallback", "model_version": "n/a"}
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401

        return {"mode": "clip", "model_version": "ViT-B-32/laion2b_s34b_b79k"}
    except ImportError:
        return {"mode": "hash_fallback", "model_version": "n/a"}


def get_dependency_versions() -> dict[str, str]:
    """Get versions of key dependencies."""
    versions: dict[str, str] = {"python": sys.version}
    for pkg in ["mujoco", "pydantic", "pint", "numpy", "scipy", "pytransform3d", "hypothesis"]:
        try:
            mod = __import__(pkg)
            versions[pkg] = getattr(mod, "__version__", "unknown")
        except ImportError:
            versions[pkg] = "not installed"
    return versions


def create_run_directory(base_dir: str | Path, name: str) -> Path:
    """Create a timestamped run directory.

    Format: <base_dir>/<YYYYMMDD-HHMMSS>-<name>/

    Args:
        base_dir: Parent directory for runs.
        name: Short name for this run.

    Returns:
        Path to the created run directory.
    """
    ts = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(base_dir) / f"{ts}-{name}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_manifest(
    run_dir: Path,
    config: dict[str, object],
    seed: int,
    metrics: dict[str, object],
    agent_trace: list[dict[str, object]] | None = None,
) -> Path:
    """Write a run manifest to the run directory.

    Args:
        run_dir: The run directory.
        config: The experiment configuration used.
        seed: The seed for this specific run.
        metrics: Computed metrics.
        agent_trace: Optional agent trace data to save alongside manifest.

    Returns:
        Path to the manifest file.
    """
    manifest = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "git_commit": get_git_commit(),
        "seed": seed,
        "config": config,
        "dependencies": get_dependency_versions(),
        "vla": get_vla_info(),
        "metrics": metrics,
    }

    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, default=str))

    if agent_trace:
        trace_path = run_dir / "agent_trace.json"
        trace_path.write_text(json.dumps(agent_trace, indent=2, default=str))
        manifest["agent_trace_file"] = "agent_trace.json"
        # Re-write manifest with the trace file reference
        path.write_text(json.dumps(manifest, indent=2, default=str))

    return path
