"""コネクタの監査ログ（SQLite、`grounding/ledger.py` と同じ設計思想）。

全ての交換要求（許可・拒否いずれも）を記録する。poc_plan.md 5.5「監査ログ」および
EXP-09「ODRLポリシー違反の監査ログ」の集計元になる。
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_at TEXT NOT NULL,
    requester_site TEXT NOT NULL,
    purpose TEXT NOT NULL,
    item_type TEXT NOT NULL,
    granted INTEGER NOT NULL,
    reason TEXT NOT NULL,
    n_items INTEGER NOT NULL
);
"""


@dataclass
class AuditEntry:
    requested_at: datetime
    requester_site: str
    purpose: str
    item_type: str
    granted: bool
    reason: str
    n_items: int


class AuditLog:
    """1拠点のコネクタが持つ監査台帳。"""

    def __init__(self, db_path: str | Path):
        """`check_same_thread=False` + 明示ロック：`server.py` の `ThreadingHTTPServer` が
        リクエストごとに別スレッドで `handle_request` を呼ぶため、1つの `AuditLog` インスタンスが
        複数スレッドから使われる。sqlite3 のデフォルト（`check_same_thread=True`）はこれを
        `ProgrammingError` で拒否するため、明示的に許可した上でロックにより直列化する
        （実機の Docker 経由 `/exchange` 呼び出しで実際に再現・確認済みのバグ修正）。
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def record(self, entry: AuditEntry) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO audit_log
                   (requested_at, requester_site, purpose, item_type, granted, reason, n_items)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    entry.requested_at.isoformat(),
                    entry.requester_site,
                    entry.purpose,
                    entry.item_type,
                    int(entry.granted),
                    entry.reason,
                    entry.n_items,
                ),
            )
            self._conn.commit()

    def count_violations(self) -> int:
        """`granted=0`（ポリシー違反で拒否された）件数。EXP-09 の指標。"""
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM audit_log WHERE granted = 0")
            return int(cur.fetchone()[0])

    def all_entries(self) -> list[dict[str, object]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM audit_log ORDER BY id")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()
