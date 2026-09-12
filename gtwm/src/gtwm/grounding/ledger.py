"""乖離台帳（C9）：poc_plan.md 5.4 のスキーマ表と一致させる SQLite 実装。

状態遷移は open → confirmed/dismissed → resolved のみ。poc_plan.md 5.4
「優先ポリシー」：「PoCでは自動反映は行わず、全件人が判定する」ため、`open` からの
遷移（confirm/dismiss/resolve）はすべて `resolver`（判定した人）を必須とする。
`open` の生成（検知）だけは検知パイプラインが行ってよい。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DISCREPANCY_TYPES = {
    "unscanned_move",
    "wrong_slot",
    "wrong_scan",
    "late_registration",
    "ghost_stock",
    "other",
}
SEVERITIES = {"low", "medium", "high"}
STATUSES = {"open", "confirmed", "dismissed", "resolved"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS discrepancies (
    discrepancy_id TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    physical_value TEXT NOT NULL,
    physical_confidence REAL NOT NULL,
    physical_source TEXT NOT NULL,
    physical_valid_time TEXT NOT NULL,
    record_value TEXT NOT NULL,
    record_source_system TEXT NOT NULL,
    record_registration_time TEXT NOT NULL,
    discrepancy_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    epsilon_at_detection REAL NOT NULL,
    status TEXT NOT NULL,
    resolution TEXT,
    resolver TEXT,
    feedback_target TEXT
);
"""


@dataclass
class DiscrepancyEntry:
    discrepancy_id: str
    object_id: str
    physical_value: str
    physical_confidence: float
    physical_source: str
    physical_valid_time: datetime
    record_value: str
    record_source_system: str
    record_registration_time: datetime
    discrepancy_type: str
    severity: str
    detected_at: datetime
    epsilon_at_detection: float
    status: str = "open"
    resolution: str | None = None
    resolver: str | None = None
    feedback_target: str | None = None


class DiscrepancyLedger:
    """`grounding/ledger.py` の SQLite バックエンド。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        self.last_create_was_duplicate = False

    def create(self, entry: DiscrepancyEntry) -> None:
        """新規エントリを追加する。

        `discrepancy_id` はエピソード・個体・ホライズン・フレーム番号から決定的に
        導出されるため、同一エピソードに対して `gtwm ground run` を再実行すると
        同じ ID が再生成される。台帳ファイルはエピソード単位で永続化される設計
        （poc_plan.md 5.4：台帳は継続的な記録）なので、同一 ID の再検知は「新しい
        乖離」ではなく「既に記録済みの乖離を再度検知した」ことを意味する。よって
        既存 ID は例外にせず黙って冪等にスキップする（内容の食い違いは検知ロジック
        が決定的である限り発生しない前提）。
        """
        if entry.discrepancy_type not in DISCREPANCY_TYPES:
            raise ValueError(f"未知の discrepancy_type: {entry.discrepancy_type}")
        if entry.severity not in SEVERITIES:
            raise ValueError(f"未知の severity: {entry.severity}")
        if entry.status != "open":
            raise ValueError("新規エントリは status='open' でなければならない（検知直後の状態）")
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO discrepancies (
                discrepancy_id, object_id, physical_value, physical_confidence,
                physical_source, physical_valid_time, record_value, record_source_system,
                record_registration_time, discrepancy_type, severity, detected_at,
                epsilon_at_detection, status, resolution, resolver, feedback_target
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                entry.discrepancy_id,
                entry.object_id,
                entry.physical_value,
                entry.physical_confidence,
                entry.physical_source,
                entry.physical_valid_time.isoformat(),
                entry.record_value,
                entry.record_source_system,
                entry.record_registration_time.isoformat(),
                entry.discrepancy_type,
                entry.severity,
                entry.detected_at.isoformat(),
                entry.epsilon_at_detection,
                entry.status,
                entry.resolution,
                entry.resolver,
                entry.feedback_target,
            ),
        )
        self._conn.commit()
        self.last_create_was_duplicate = cur.rowcount == 0

    def _get_status(self, discrepancy_id: str) -> str:
        row = self._conn.execute(
            "SELECT status FROM discrepancies WHERE discrepancy_id = ?", (discrepancy_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"未知の discrepancy_id: {discrepancy_id}")
        return str(row[0])

    def confirm(self, discrepancy_id: str, resolver: str) -> None:
        """人がこの乖離を「実在する」と判定する（open -> confirmed）。"""
        if self._get_status(discrepancy_id) != "open":
            raise ValueError("confirm は status='open' からのみ遷移できる")
        self._conn.execute(
            "UPDATE discrepancies SET status='confirmed', resolver=? WHERE discrepancy_id=?",
            (resolver, discrepancy_id),
        )
        self._conn.commit()

    def dismiss(self, discrepancy_id: str, resolver: str) -> None:
        """人がこの乖離を「誤報」と判定する（open -> dismissed。終端状態）。"""
        if self._get_status(discrepancy_id) != "open":
            raise ValueError("dismiss は status='open' からのみ遷移できる")
        self._conn.execute(
            "UPDATE discrepancies SET status='dismissed', resolver=? WHERE discrepancy_id=?",
            (resolver, discrepancy_id),
        )
        self._conn.commit()

    def resolve(
        self,
        discrepancy_id: str,
        resolver: str,
        resolution: str,
        feedback_target: str | None = None,
    ) -> None:
        """confirmed な乖離を、人が対応内容を記録して解決する（confirmed -> resolved）。"""
        if self._get_status(discrepancy_id) != "confirmed":
            raise ValueError("resolve は status='confirmed' からのみ遷移できる")
        self._conn.execute(
            """UPDATE discrepancies
               SET status='resolved', resolver=?, resolution=?, feedback_target=?
               WHERE discrepancy_id=?""",
            (resolver, resolution, feedback_target, discrepancy_id),
        )
        self._conn.commit()

    def get(self, discrepancy_id: str) -> dict[str, object]:
        row = self._conn.execute(
            "SELECT * FROM discrepancies WHERE discrepancy_id = ?", (discrepancy_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"未知の discrepancy_id: {discrepancy_id}")
        columns = [
            d[0] for d in self._conn.execute("SELECT * FROM discrepancies LIMIT 0").description
        ]
        return dict(zip(columns, row, strict=True))

    def list_by_status(self, status: str) -> list[dict[str, object]]:
        if status not in STATUSES:
            raise ValueError(f"未知の status: {status}")
        rows = self._conn.execute(
            "SELECT * FROM discrepancies WHERE status = ?", (status,)
        ).fetchall()
        columns = [
            d[0] for d in self._conn.execute("SELECT * FROM discrepancies LIMIT 0").description
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def close(self) -> None:
        self._conn.close()


__all__ = [
    "DISCREPANCY_TYPES",
    "SEVERITIES",
    "STATUSES",
    "DiscrepancyEntry",
    "DiscrepancyLedger",
]
