"""Tests for the log↔metrics reconciliation audit (M12 acceptance gate)."""

import json
from pathlib import Path

import pytest

from mws.eval.reconcile import count_log_markers, reconcile, sum_run_metrics

_LOG = """
2026-06-11 [debug] Gemini embedding call dims=768 model=gemini-embedding-2
2026-06-11 [debug] Ingested atom atom_id=x modality=pose
2026-06-11 [debug] Gemini embedding call dims=768 model=gemini-embedding-2
2026-06-11 [debug] ADK call agent=ops_agent model=gemini-3.5-flash
"""


def _make_run(tmp_path: Path, name: str, emb: int, real: int, *, legacy_key: bool = False) -> Path:
    d = tmp_path / name
    d.mkdir()
    key = "embedding_calls" if legacy_key else "embedding_requests"
    (d / "metrics.json").write_text(json.dumps({"system": {key: emb, "llm_calls_real": real}}))
    return d


def test_count_log_markers() -> None:
    counts = count_log_markers(_LOG)
    assert counts == {"embedding_requests": 2, "llm_calls_real": 1}


def test_reconcile_zero_delta(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text(_LOG)
    runs = [_make_run(tmp_path, "r1", emb=1, real=1), _make_run(tmp_path, "r2", emb=1, real=0)]
    result = reconcile(log, runs)
    assert result["reconciled"] is True
    assert result["delta"] == {"embedding_requests": 0, "llm_calls_real": 0}


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


def test_sum_run_metrics_missing_file_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        sum_run_metrics([empty])
