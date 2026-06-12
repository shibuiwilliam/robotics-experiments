"""Log↔metrics reconciliation audit (IMPROVEMENT M12 acceptance gate).

"Measurements tell the truth" is only provable against ground truth. For a
LIVE run, the ground truth of cloud usage is the run log itself:

- every real Gemini Embedding 2 API REQUEST logs one ``Gemini embedding call``
  line (mws/embedding/teacher.py — a batched request embedding N texts logs
  ONE line with n_texts=N, and counts as ONE request in metrics; E2),
- every real ADK LLM call logs one ``ADK call`` line (mws/agents/live.py), and
- every real ADK LLM call appends one line to ``runs/<RUN_ID>/llm_calls.jsonl``
  (mws/core/llm_log.py) — the THIRD leg (M19), so the recorder itself is
  audited rather than trusted.

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


def sum_recorded_llm_calls(run_dirs: list[Path]) -> int:
    """Third audit leg (M19): recorded real-LLM responses across runs.

    Counts llm_calls.jsonl lines per run dir. A missing file counts as 0 —
    consistent with mock runs, which make no real calls and create no file.
    This leg is produced INDEPENDENTLY of the log markers and the metrics
    counters (the recorder appends at call time), so a broken recorder shows
    up as a non-zero delta instead of being silently trusted.
    """
    total = 0
    for run_dir in run_dirs:
        path = run_dir / "llm_calls.jsonl"
        if path.exists():
            total += sum(1 for line in path.read_text().splitlines() if line.strip())
    return total


def reconcile(log_path: Path, run_dirs: list[Path]) -> dict[str, Any]:
    """3-way diff: log markers vs metrics counters vs recorded responses.

    The three legs are independently produced (logger lines, cost-tracker
    counters, recorder JSONL appends) — never derived from each other. The
    recorded leg is compared against BOTH other legs through llm_calls_real:
    actual carries the marker count, counted carries the metrics count, and
    ``llm_calls_recorded`` carries the JSONL line count on each side of the
    diff so a recorder failure (M19) surfaces as a non-zero delta.

    Returns {"actual": ..., "counted": ..., "delta": ..., "reconciled": bool}.
    delta = actual - counted per category; reconciled iff all deltas are 0.
    """
    actual = count_log_markers(Path(log_path).read_text())
    counted = sum_run_metrics(run_dirs)
    recorded = sum_recorded_llm_calls(run_dirs)
    # Third leg: recorded responses must equal the real-call count seen in the
    # LOG (actual side) and in the METRICS (counted side). Expressing it as
    # actual=marker-count vs counted=recorded-count diffs it against both:
    # marker==metrics is already enforced by llm_calls_real itself.
    actual["llm_calls_recorded"] = actual["llm_calls_real"]
    counted["llm_calls_recorded"] = recorded
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
