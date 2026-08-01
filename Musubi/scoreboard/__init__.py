"""Measurement — the Epistemic Scoreboard (DuckDB metrics: belief accuracy, calibration/ECE,
freshness, MTTC…), the semantic-observability dashboard, and report generation. Read-only
observer: never writes to production paths (PROJECT.md §6.2).
"""

from __future__ import annotations
