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
    plan_steps INTEGER, executed INTEGER, reason TEXT
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

    def ingest(self, records: list[dict[str, Any]]) -> int:
        rows = [[r.get(c) for c in _COLUMNS] for r in records]
        placeholders = ",".join(["?"] * len(_COLUMNS))
        self._con.executemany(f"INSERT INTO runs VALUES ({placeholders})", rows)
        return len(rows)

    def clear(self, scenario: str | None = None) -> None:
        if scenario:
            self._con.execute("DELETE FROM runs WHERE scenario = ?", [scenario])
        else:
            self._con.execute("DELETE FROM runs")

    def arm_summaries(self, scenario: str | None = None) -> list[ArmSummary]:
        where = "WHERE scenario = ?" if scenario else ""
        params = [scenario] if scenario else []
        rows = self._con.execute(
            f"""
            SELECT scenario, arm, COUNT(*) AS n,
                   AVG(CAST(oracle_passed AS DOUBLE)) AS oracle_rate,
                   AVG(CAST(success AS DOUBLE)) AS success_rate,
                   SUM(unapproved_irreversible) AS unappr,
                   AVG(trace_completeness) AS mean_trace,
                   SUM(api_calls) AS total_api
            FROM runs {where}
            GROUP BY scenario, arm
            ORDER BY scenario, arm
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
