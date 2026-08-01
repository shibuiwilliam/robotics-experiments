"""SQLite-backed, append-only, bitemporal Claim store."""

from __future__ import annotations

import sqlite3
from typing import Any

from core.claimstore.decay import decayed_confidence
from core.clock import SimClock
from ontology.generated.musubi_types import Claim

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    iri              TEXT PRIMARY KEY,
    subject          TEXT,
    predicate        TEXT,
    realm            TEXT,
    claim_kind       TEXT,
    supersedes       TEXT,
    transaction_time REAL,
    seq              INTEGER,
    json             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject);
CREATE INDEX IF NOT EXISTS idx_claims_supersedes ON claims(supersedes);
"""


class AppendOnlyViolation(RuntimeError):
    """Raised on any attempt to re-insert an existing Claim IRI (append-only, CLAUDE.md §0-3)."""


class ClaimStore:
    """Append-only bitemporal Claim store. Never updates or deletes an existing Claim."""

    def __init__(self, clock: SimClock, db_path: str | None = None) -> None:
        self._clock = clock
        self._conn = sqlite3.connect(db_path or ":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._seq = 0

    # -- writes (append only) ------------------------------------------------
    def add(self, claim: Claim) -> Claim:
        """Append a Claim, stamping transaction/valid time from the sim clock. Returns the stamped copy."""
        now = self._clock.now()
        txn = self._clock.tick_transaction()
        updates: dict[str, Any] = {}
        if getattr(claim, "transactionTime", None) is None:
            updates["transactionTime"] = txn
        if getattr(claim, "createdAt", None) is None:
            updates["createdAt"] = txn
        if getattr(claim, "validTime", None) is None:
            updates["validTime"] = now
        stamped = claim.model_copy(update=updates) if updates else claim
        txn_time = txn if stamped.transactionTime is None else float(stamped.transactionTime)
        self._seq += 1
        try:
            self._conn.execute(
                "INSERT INTO claims (iri, subject, predicate, realm, claim_kind, supersedes, "
                "transaction_time, seq, json) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    str(stamped.iri),
                    _s(stamped.subject),
                    _s(stamped.predicate),
                    _s(stamped.realm),
                    _s(stamped.claimKind),
                    _s(stamped.supersedes),
                    txn_time,
                    self._seq,
                    stamped.model_dump_json(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise AppendOnlyViolation(
                f"Claim {stamped.iri} already exists; Claims are append-only (use supersede)."
            ) from exc
        self._conn.commit()
        return stamped

    def supersede(self, old_iri: str, new_claim: Claim) -> Claim:
        """Append ``new_claim`` as the replacement of ``old_iri`` (the old Claim is never deleted)."""
        if self.get(old_iri) is None:
            raise KeyError(f"cannot supersede unknown Claim {old_iri}")
        return self.add(new_claim.model_copy(update={"supersedes": old_iri}))

    # -- reads ---------------------------------------------------------------
    def get(self, iri: str) -> Claim | None:
        row = self._conn.execute("SELECT json FROM claims WHERE iri = ?", (iri,)).fetchone()
        return Claim.model_validate_json(row["json"]) if row else None

    def all(self) -> list[Claim]:
        rows = self._conn.execute("SELECT json FROM claims ORDER BY seq").fetchall()
        return [Claim.model_validate_json(r["json"]) for r in rows]

    def _superseded_iris(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT supersedes FROM claims WHERE supersedes IS NOT NULL"
        ).fetchall()
        return {r["supersedes"] for r in rows}

    def active(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        realm: str | None = None,
    ) -> list[Claim]:
        """Active (non-superseded) Claims, optionally filtered by subject/predicate/realm."""
        superseded = self._superseded_iris()
        sql = "SELECT iri, json FROM claims WHERE 1=1"
        params: list[Any] = []
        if subject is not None:
            sql += " AND subject = ?"
            params.append(subject)
        if predicate is not None:
            sql += " AND predicate = ?"
            params.append(predicate)
        if realm is not None:
            sql += " AND realm = ?"
            params.append(realm)
        sql += " ORDER BY seq"
        rows = self._conn.execute(sql, params).fetchall()
        return [Claim.model_validate_json(r["json"]) for r in rows if r["iri"] not in superseded]

    def history(self, subject: str) -> list[Claim]:
        """All Claims about ``subject`` in transaction-time order (append order)."""
        rows = self._conn.execute(
            "SELECT json FROM claims WHERE subject = ? ORDER BY seq", (subject,)
        ).fetchall()
        return [Claim.model_validate_json(r["json"]) for r in rows]

    def as_of(self, transaction_time: float) -> list[Claim]:
        """Bitemporal slice: Claims known as of ``transaction_time`` (S5 forensic replay)."""
        rows = self._conn.execute(
            "SELECT json FROM claims WHERE transaction_time <= ? ORDER BY seq",
            (transaction_time,),
        ).fetchall()
        return [Claim.model_validate_json(r["json"]) for r in rows]

    def decayed_confidence(self, claim: Claim, at_time: float | None = None) -> float:
        return decayed_confidence(claim, self._clock.now() if at_time is None else at_time)

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM claims").fetchone()["n"])

    def close(self) -> None:
        self._conn.close()


def _s(value: Any) -> str | None:
    """Stringify an enum/uri value for an indexed column (None-safe)."""
    if value is None:
        return None
    return getattr(value, "value", None) or str(value)
