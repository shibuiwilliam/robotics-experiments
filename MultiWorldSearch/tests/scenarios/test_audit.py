"""Tests for the JSONL audit logger."""

from pathlib import Path

from mws.scenarios.audit import AuditLogger


def test_entry_count_survives_flush(tmp_path: Path) -> None:
    """IMPROVEMENT M6: entry_count is cumulative — flushing the buffer must not
    reset it (scenarios log the total AFTER flushing)."""
    audit = AuditLogger(tmp_path, "run-x")
    audit.log("setup", "system", "a")
    audit.log("act", "agent", "b")
    assert audit.entry_count == 2
    audit.flush()
    assert audit.entry_count == 2  # cumulative, not buffer length
    audit.log("evaluate", "system", "c")
    audit.flush()
    assert audit.entry_count == 3
    # All entries are on disk exactly once.
    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3
