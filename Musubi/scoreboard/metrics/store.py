"""DuckDB-backed metrics store (Epistemic Scoreboard, PROJECT.md §8).

Ingests run records and aggregates them per (scenario, arm) — order-fulfilment / oracle-pass rate,
mean trace completeness, total unapproved-irreversible (the must-be-zero), API calls. Read-only
observer: it never writes to the system path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _REPO_ROOT / "data" / "scoreboard.duckdb"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    scenario TEXT, experiment TEXT, arm TEXT, seed INTEGER,
    oracle_passed BOOLEAN, success BOOLEAN,
    unapproved_irreversible INTEGER, trace_completeness DOUBLE,
    claims INTEGER, bus_events INTEGER, api_calls INTEGER,
    plan_steps INTEGER, executed INTEGER, reason TEXT,
    run_id BIGINT
);
"""

_COLUMNS = [
    "scenario",
    "experiment",
    "arm",
    "seed",
    "oracle_passed",
    "success",
    "unapproved_irreversible",
    "trace_completeness",
    "claims",
    "bus_events",
    "api_calls",
    "plan_steps",
    "executed",
    "reason",
]


@dataclass
class ArmSummary:
    scenario: str
    arm: str
    n: int
    oracle_pass_rate: float
    success_rate: float
    unapproved_irreversible: int
    mean_trace_completeness: float
    total_api_calls: int


class MetricsStore:
    """Ingest + aggregate run records in DuckDB."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        path = Path(db_path) if db_path is not None else _DEFAULT_DB
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(_SCHEMA)

    def _next_run_id(self) -> int:
        # Deterministic monotonic batch id (not wallclock — keeps NFR-DETERM). Each ingest() call is
        # one batch, so arm_summaries can scope to the latest run instead of aggregating history.
        row = self._con.execute("SELECT COALESCE(MAX(run_id), 0) FROM runs").fetchone()
        return (int(row[0]) if row and row[0] is not None else 0) + 1

    def ingest(self, records: list[dict[str, Any]]) -> int:
        batch = self._next_run_id()
        cols = [*_COLUMNS, "run_id"]
        rows = [[r.get(c) for c in _COLUMNS] + [batch] for r in records]
        placeholders = ",".join(["?"] * len(cols))
        self._con.executemany(f"INSERT INTO runs ({', '.join(cols)}) VALUES ({placeholders})", rows)
        return len(rows)

    def clear(self, scenario: str | None = None) -> None:
        if scenario:
            self._con.execute("DELETE FROM runs WHERE scenario = ?", [scenario])
        else:
            self._con.execute("DELETE FROM runs")

    def arm_summaries(self, scenario: str | None = None) -> list[ArmSummary]:
        # Scope to each scenario's LATEST run batch so per-arm n/rates reflect one run, not the
        # cumulative append-only history (fixes the "n grows across invocations" observability bug).
        where = "AND r.scenario = ?" if scenario else ""
        params = [scenario] if scenario else []
        rows = self._con.execute(
            f"""
            SELECT r.scenario, r.arm, COUNT(*) AS n,
                   AVG(CAST(r.oracle_passed AS DOUBLE)) AS oracle_rate,
                   AVG(CAST(r.success AS DOUBLE)) AS success_rate,
                   SUM(r.unapproved_irreversible) AS unappr,
                   AVG(r.trace_completeness) AS mean_trace,
                   SUM(r.api_calls) AS total_api
            FROM runs r
            WHERE r.run_id = (SELECT MAX(run_id) FROM runs WHERE scenario = r.scenario) {where}
            GROUP BY r.scenario, r.arm
            ORDER BY r.scenario, r.arm
            """,
            params,
        ).fetchall()
        return [
            ArmSummary(
                scenario=r[0],
                arm=r[1],
                n=int(r[2]),
                oracle_pass_rate=float(r[3]),
                success_rate=float(r[4]),
                unapproved_irreversible=int(r[5] or 0),
                mean_trace_completeness=float(r[6] or 0.0),
                total_api_calls=int(r[7] or 0),
            )
            for r in rows
        ]

    def all_records(self) -> list[dict[str, Any]]:
        rows = self._con.execute(f"SELECT {', '.join(_COLUMNS)} FROM runs").fetchall()
        return [dict(zip(_COLUMNS, r, strict=True)) for r in rows]

    def close(self) -> None:
        self._con.close()
