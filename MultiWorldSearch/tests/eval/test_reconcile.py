"""Tests for the 3-way reconciliation audit (M12 + M19 acceptance gates).

Three independently produced legs must agree: log markers, metrics counters,
and recorded llm_calls.jsonl lines.
"""

import json
from pathlib import Path

import pytest

from mws.eval.reconcile import (
    count_log_markers,
    reconcile,
    sum_recorded_llm_calls,
    sum_run_metrics,
)

_LOG = """
2026-06-11 [debug] Gemini embedding call dims=768 model=gemini-embedding-2
2026-06-11 [debug] Ingested atom atom_id=x modality=pose
2026-06-11 [debug] Gemini embedding call dims=768 model=gemini-embedding-2
2026-06-11 [debug] ADK call agent=ops_agent model=gemini-3.5-flash
"""


def _make_run(
    tmp_path: Path,
    name: str,
    emb: int,
    real: int,
    *,
    recorded: int | None = None,
    legacy_key: bool = False,
) -> Path:
    """Synthetic run dir. ``recorded`` defaults to ``real`` (healthy recorder);
    pass a different value to simulate a broken recorder. recorded=0 writes no
    file at all — matching mock runs (lazy file creation)."""
    d = tmp_path / name
    d.mkdir()
    key = "embedding_calls" if legacy_key else "embedding_requests"
    (d / "metrics.json").write_text(json.dumps({"system": {key: emb, "llm_calls_real": real}}))
    n_recorded = real if recorded is None else recorded
    if n_recorded > 0:
        lines = [json.dumps({"purpose": f"step:{i}", "response": "ok"}) for i in range(n_recorded)]
        (d / "llm_calls.jsonl").write_text("\n".join(lines) + "\n")
    return d


def test_count_log_markers() -> None:
    counts = count_log_markers(_LOG)
    assert counts == {"embedding_requests": 2, "llm_calls_real": 1}


def test_reconcile_three_way_zero_delta(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text(_LOG)
    runs = [_make_run(tmp_path, "r1", emb=1, real=1), _make_run(tmp_path, "r2", emb=1, real=0)]
    result = reconcile(log, runs)
    assert result["reconciled"] is True
    assert result["delta"] == {
        "embedding_requests": 0,
        "llm_calls_real": 0,
        "llm_calls_recorded": 0,
    }


def test_reconcile_accepts_legacy_embedding_calls_key(tmp_path: Path) -> None:
    """Older artifacts (pre-E2) expose only embedding_calls — still readable."""
    log = tmp_path / "run.log"
    log.write_text(_LOG)
    runs = [
        _make_run(tmp_path, "r1", emb=1, real=1, legacy_key=True),
        _make_run(tmp_path, "r2", emb=1, real=0, legacy_key=True),
    ]
    assert reconcile(log, runs)["reconciled"] is True


def test_reconcile_detects_uncounted_calls(tmp_path: Path) -> None:
    """Falsifiability: an uncounted real request (log has more than metrics)
    produces a positive delta and reconciled=False — the M12 leak class."""
    log = tmp_path / "run.log"
    log.write_text(_LOG)
    runs = [_make_run(tmp_path, "r1", emb=1, real=1)]  # 1 embed missing
    result = reconcile(log, runs)
    assert result["reconciled"] is False
    assert result["delta"]["embedding_requests"] == 1


def test_reconcile_detects_missing_recording(tmp_path: Path) -> None:
    """Falsifiability (M19): a real call whose response was NOT recorded
    (broken recorder) fails the audit even though log==metrics."""
    log = tmp_path / "run.log"
    log.write_text(_LOG)
    runs = [
        _make_run(tmp_path, "r1", emb=1, real=1, recorded=0),  # recorder failed
        _make_run(tmp_path, "r2", emb=1, real=0),
    ]
    result = reconcile(log, runs)
    assert result["reconciled"] is False
    assert result["delta"]["llm_calls_recorded"] == 1
    # The other two legs still agree — only the recording leg is broken.
    assert result["delta"]["llm_calls_real"] == 0


def test_reconcile_detects_excess_recordings(tmp_path: Path) -> None:
    """Falsifiability (M19): more recordings than real calls (e.g. replay
    accidentally re-recording) also fails the audit."""
    log = tmp_path / "run.log"
    log.write_text(_LOG)
    runs = [_make_run(tmp_path, "r1", emb=2, real=1, recorded=3)]
    result = reconcile(log, runs)
    assert result["reconciled"] is False
    assert result["delta"]["llm_calls_recorded"] == -2


def test_mock_runs_reconcile_cleanly(tmp_path: Path) -> None:
    """Mock: no markers, real=0, no llm_calls.jsonl file → zero delta."""
    log = tmp_path / "run.log"
    log.write_text("2026-06-11 [info] nothing cloudy here\n")
    runs = [_make_run(tmp_path, "r1", emb=0, real=0)]
    assert reconcile(log, runs)["reconciled"] is True


def test_sum_recorded_handles_missing_files(tmp_path: Path) -> None:
    runs = [
        _make_run(tmp_path, "r1", emb=0, real=2),  # 2 recorded lines
        _make_run(tmp_path, "r2", emb=0, real=0),  # no file
    ]
    assert sum_recorded_llm_calls(runs) == 2


def test_sum_run_metrics_missing_file_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        sum_run_metrics([empty])
