"""Timeseries store for high-frequency telemetry data.

Two interchangeable backends with the same interface:
- ``TimeseriesStore`` — dependency-light in-memory sorted list (default).
- ``DuckDBTimeseriesStore`` — real DuckDB (embedded SQL) backend, selected via
  the store registry / index config. Time-range and tag filtering run as SQL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class TimeseriesRecord:
    """A single timeseries data point."""

    atom_id: str
    timestamp: float
    values: dict[str, float]
    tags: dict[str, str] = field(default_factory=dict)


class TimeseriesStore:
    """In-memory timeseries store.

    For the prototype, keeps records in a sorted list.
    DuckDB/Parquet can be swapped in for larger scale.
    """

    def __init__(self) -> None:
        self._records: list[TimeseriesRecord] = []

    def add(self, record: TimeseriesRecord) -> None:
        """Insert a record, maintaining time order."""
        self._records.append(record)
        self._records.sort(key=lambda r: r.timestamp)

    def query_range(
        self,
        start: float,
        end: float,
        tag_filter: dict[str, str] | None = None,
    ) -> list[TimeseriesRecord]:
        """Query records in a time range with optional tag filter."""
        results = []
        for r in self._records:
            if r.timestamp < start:
                continue
            if r.timestamp > end:
                break
            if tag_filter:
                if all(r.tags.get(k) == v for k, v in tag_filter.items()):
                    results.append(r)
            else:
                results.append(r)
        return results

    def latest(
        self, n: int = 1, tag_filter: dict[str, str] | None = None
    ) -> list[TimeseriesRecord]:
        """Get the n most recent records."""
        if tag_filter:
            filtered = [
                r for r in self._records if all(r.tags.get(k) == v for k, v in tag_filter.items())
            ]
            return filtered[-n:]
        return self._records[-n:]

    @property
    def size(self) -> int:
        return len(self._records)


class DuckDBTimeseriesStore:
    """DuckDB-backed timeseries store (embedded SQL, in-process).

    Same interface as :class:`TimeseriesStore`, but time-range and tag queries
    execute as real SQL against DuckDB rather than Python list scans. Uses an
    in-memory DuckDB connection by default (no files).
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        import duckdb

        self._con = duckdb.connect(db_path)
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS telemetry ("
            "atom_id VARCHAR, ts DOUBLE, vals JSON, tags JSON)"
        )

    def add(self, record: TimeseriesRecord) -> None:
        self._con.execute(
            "INSERT INTO telemetry VALUES (?, ?, ?, ?)",
            [record.atom_id, record.timestamp, json.dumps(record.values), json.dumps(record.tags)],
        )

    def _row_to_record(self, row: tuple) -> TimeseriesRecord:
        atom_id, ts, vals, tags = row
        return TimeseriesRecord(
            atom_id=atom_id,
            timestamp=float(ts),
            values=json.loads(vals) if vals else {},
            tags=json.loads(tags) if tags else {},
        )

    def query_range(
        self,
        start: float,
        end: float,
        tag_filter: dict[str, str] | None = None,
    ) -> list[TimeseriesRecord]:
        sql = "SELECT atom_id, ts, vals, tags FROM telemetry WHERE ts >= ? AND ts <= ?"
        params: list[object] = [start, end]
        if tag_filter:
            for k, v in tag_filter.items():
                # SQL-side tag match via JSON extraction.
                sql += " AND json_extract_string(tags, ?) = ?"
                params.extend([f"$.{k}", v])
        sql += " ORDER BY ts"
        rows = self._con.execute(sql, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def latest(
        self, n: int = 1, tag_filter: dict[str, str] | None = None
    ) -> list[TimeseriesRecord]:
        sql = "SELECT atom_id, ts, vals, tags FROM telemetry"
        params: list[object] = []
        if tag_filter:
            clauses = []
            for k, v in tag_filter.items():
                clauses.append("json_extract_string(tags, ?) = ?")
                params.extend([f"$.{k}", v])
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(n)
        rows = self._con.execute(sql, params).fetchall()
        # Return in ascending time order to match the in-memory store's `[-n:]`.
        return [self._row_to_record(r) for r in reversed(rows)]

    @property
    def size(self) -> int:
        row = self._con.execute("SELECT COUNT(*) FROM telemetry").fetchone()
        return int(row[0]) if row is not None else 0
