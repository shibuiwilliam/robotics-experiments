"""Log↔metrics reconciliation audit (IMPROVEMENT M12 acceptance gate).

"Measurements tell the truth" is only provable against ground truth. For a
LIVE run, the ground truth of cloud usage is the run log itself:

- every real Gemini Embedding 2 API REQUEST logs one ``Gemini embedding call``
  line (mws/embedding/teacher.py — a batched request embedding N texts logs
  ONE line with n_texts=N, and counts as ONE request in metrics; E2), and
- every real ADK LLM call logs one ``ADK call`` line (mws/agents/live.py).

This module counts those lines and diffs them against the summed metrics of
the run's artifacts. A non-zero delta means a real cloud call escaped the
trackers — exactly the class of leak found (and fixed) in IMPROVEMENT M12.
Use after every live `make scenario-all`:

    uv run python -m mws.cli eval reconcile --log <logfile> --runs <id1,id2,...>

Notes:
- Only meaningful for LIVE runs (mock embedders/agents emit no marker lines).
- The log must cover exactly the runs being summed (one invocation's output).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mws.core.logging import get_logger

logger = get_logger(__name__)

EMBED_MARKER = "Gemini embedding call"
ADK_MARKER = "ADK call"


def count_log_markers(log_text: str) -> dict[str, int]:
    """Count actual-cloud-request marker lines in a run log."""
    return {
        "embedding_requests": log_text.count(EMBED_MARKER),
        "llm_calls_real": log_text.count(ADK_MARKER),
    }


def sum_run_metrics(run_dirs: list[Path]) -> dict[str, int]:
    """Sum the tracker-counted cloud requests across run artifacts."""
    totals = {"embedding_requests": 0, "llm_calls_real": 0}
    for run_dir in run_dirs:
        metrics_path = run_dir / "metrics.json"
        if not metrics_path.exists():
            raise FileNotFoundError(f"missing metrics.json in {run_dir}")
        system = json.loads(metrics_path.read_text()).get("system", {})
        # E2 split key; fall back to the pre-split alias for older artifacts.
        requests = system.get("embedding_requests", system.get("embedding_calls", 0))
        totals["embedding_requests"] += int(requests)
        totals["llm_calls_real"] += int(system.get("llm_calls_real", 0))
    return totals


def reconcile(log_path: Path, run_dirs: list[Path]) -> dict[str, Any]:
    """Diff actual (log markers) vs counted (metrics) cloud calls.

    Returns {"actual": ..., "counted": ..., "delta": ..., "reconciled": bool}.
    delta = actual - counted per category; reconciled iff all deltas are 0.
    """
    actual = count_log_markers(Path(log_path).read_text())
    counted = sum_run_metrics(run_dirs)
    delta = {k: actual[k] - counted[k] for k in actual}
    reconciled = all(v == 0 for v in delta.values())
    result = {
        "log": str(log_path),
        "n_runs": len(run_dirs),
        "actual": actual,
        "counted": counted,
        "delta": delta,
        "reconciled": reconciled,
    }
    if not reconciled:
        logger.warning("Reconciliation FAILED — uncounted real cloud calls", **delta)
    else:
        logger.info("Reconciliation OK — zero delta", **actual)
    return result
