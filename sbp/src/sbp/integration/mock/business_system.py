"""台本付きの業務システムモック（SQLite）。

設計書 §14 のワークドエグザンプル（ライン 3 のポンプ P-101）を業務側から見た形で再現する。
モックが提供するのは「業務システムが何と言っているか」だけで、物理の真値は持たない
（真値は ``sbp.testing`` の ``SynthWorld``。不変条件 10）。

台本には**意図的な誤り**を仕込める。設計書 §0.2 の仮定 A3「業務記録は誤りうる」を検証に
乗せるため、遅延記帳・幽霊在庫・状態の食い違いを再現できる必要がある。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Self

from sbp.core.logging import get_logger
from sbp.core.time import ensure_utc, now
from sbp.integration.connectors import RawRecord, SchemaFingerprint

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path

__all__ = ["DEFAULT_SCENARIO", "MockBusinessSystem", "Scenario", "ScenarioStep"]

log = get_logger(__name__)

# 業務システム側のテーブル定義。列名は業務語彙（オントロジーの語彙ではない）。
# 写像（写像 DSL、ADR-0006）がこれをオントロジーへ移す。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_order (
    wo_number    TEXT PRIMARY KEY,
    asset_tag    TEXT NOT NULL,
    wo_type      TEXT NOT NULL,
    status       TEXT NOT NULL,
    planned_date TEXT,
    completed_at TEXT,
    recorded_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operation_log (
    log_id       TEXT PRIMARY KEY,
    asset_tag    TEXT NOT NULL,
    running      INTEGER NOT NULL,
    shift        TEXT NOT NULL,
    logged_at    TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spare_part_stock (
    sku          TEXT NOT NULL,
    location     TEXT NOT NULL,
    quantity     INTEGER NOT NULL,
    counted_at   TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (sku, location)
);
CREATE TABLE IF NOT EXISTS maintenance_plan (
    plan_id      TEXT PRIMARY KEY,
    asset_tag    TEXT NOT NULL,
    plan_type    TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end   TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_register (
    asset_tag    TEXT PRIMARY KEY,
    asset_type   TEXT NOT NULL,
    part_of      TEXT,
    recorded_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""

_TABLE_TO_SOURCE: dict[str, str] = {
    "work_order": "https://sbp.example/ent#CmmsSource",
    "operation_log": "https://sbp.example/ent#MesSource",
    "spare_part_stock": "https://sbp.example/ent#ErpSource",
    "maintenance_plan": "https://sbp.example/ent#CmmsSource",
    # 設備台帳は CMMS が持つ（設計書 §7.1 のマスタ。ADR-0016）
    "asset_register": "https://sbp.example/ent#CmmsSource",
}

_PRIMARY_KEY: dict[str, tuple[str, ...]] = {
    "work_order": ("wo_number",),
    "operation_log": ("log_id",),
    "spare_part_stock": ("sku", "location"),
    "maintenance_plan": ("plan_id",),
    "asset_register": ("asset_tag",),
}


@dataclass(frozen=True, slots=True)
class ScenarioStep:
    """台本の 1 手。

    Attributes:
        table: 対象テーブル。
        values: 列名 → 値。``updated_at`` は省略すると ``recorded_at`` と同じになる。
        note: この手が何を表すか（意図的な誤りの説明を含む）。テストの意図を残すために使う。
    """

    table: str
    values: dict[str, Any]
    note: str = ""


@dataclass(frozen=True, slots=True)
class Scenario:
    """業務システムの台本。

    Attributes:
        name: 台本の名前。
        steps: 適用する手の列（順序を持つ）。
    """

    name: str
    steps: tuple[ScenarioStep, ...] = field(default_factory=tuple)


def _iso(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def _parse_or_now(value: object) -> datetime:
    """ISO 文字列を UTC datetime にする。解釈できなければ現在時刻を返す。"""
    if isinstance(value, str):
        try:
            return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return now()
    return now()


def build_default_scenario(t0: datetime) -> Scenario:
    """設計書 §14 に対応する既定の台本を作る。

    含まれる主張（いずれも業務側の言い分であり、物理の真値ではない）:

    1. MES は 09:00 から稼働中と言っている（実際は 09:41 に停止する → 状態不一致 類型 2）。
    2. CMMS に軸受交換の作業指示があり、完了は実作業の翌日に記録されている（→ 時間不一致 類型 3）。
    3. ERP は予備品 SKU-77 を 3 個あると言っている（実際は 0 個 → 存在不一致 類型 1、幽霊在庫）。
    4. 保全計画は 09:00〜17:00 の稼働を予定している（遵守率の事前分布を伴う）。
    5. 設備台帳はライン 3 にポンプ P-101 と P-102 があると言っている（ADR-0016。接地対象と業務価値の
       引き当てはここから始まる）。

    Args:
        t0: 台本の基準時刻（設計書 §14 の 2026-09-12 09:00 相当）。
    """
    day = _iso(t0)
    registered = _iso(t0 - timedelta(days=30))
    return Scenario(
        name="design-§14-equipment-maintenance",
        steps=(
            ScenarioStep(
                table="asset_register",
                values={
                    "asset_tag": "LINE-3",
                    "asset_type": "ProductionLine",
                    "recorded_at": registered,
                },
                note="ライン 3（部分−全体の全体側）",
            ),
            ScenarioStep(
                table="asset_register",
                values={
                    "asset_tag": "P-101",
                    "asset_type": "Pump",
                    "part_of": "LINE-3",
                    "recorded_at": registered,
                },
                note="設計書 §14 のポンプ",
            ),
            ScenarioStep(
                table="asset_register",
                values={
                    "asset_tag": "P-102",
                    "asset_type": "Pump",
                    "part_of": "LINE-3",
                    "recorded_at": registered,
                },
                note="同じラインのもう 1 台（部分−全体の結合が 2 部分になる）",
            ),
            ScenarioStep(
                table="operation_log",
                values={
                    "log_id": "OL-0912-A",
                    "asset_tag": "P-101",
                    "running": 1,
                    "shift": "shift1",
                    "logged_at": day,
                    "recorded_at": _iso(t0 + timedelta(minutes=5)),
                },
                note="MES は稼働中と主張（物理は 09:41 に停止する。類型 2 の材料）",
            ),
            ScenarioStep(
                table="work_order",
                values={
                    "wo_number": "WO-123",
                    "asset_tag": "P-101",
                    "wo_type": "BearingReplacement",
                    "status": "Completed",
                    "planned_date": day,
                    "completed_at": _iso(t0 + timedelta(hours=8)),
                    "recorded_at": _iso(t0 + timedelta(days=1, hours=2)),
                },
                note="完了記録が実作業の翌日（遅延記帳。類型 3 の材料）",
            ),
            ScenarioStep(
                table="spare_part_stock",
                values={
                    "sku": "SKU-77",
                    "location": "WH-A-01",
                    "quantity": 3,
                    "counted_at": day,
                    "recorded_at": _iso(t0 + timedelta(hours=1)),
                },
                note="ERP は 3 個あると主張（物理は 0 個。幽霊在庫。類型 1 の材料）",
            ),
            ScenarioStep(
                table="maintenance_plan",
                values={
                    "plan_id": "MP-0912",
                    "asset_tag": "P-101",
                    "plan_type": "PlannedOperation",
                    "window_start": day,
                    "window_end": _iso(t0 + timedelta(hours=8)),
                    "recorded_at": _iso(t0 - timedelta(days=7)),
                },
                note="09:00〜17:00 の稼働予定（遵守率の事前分布を伴う planned アサーションになる）",
            ),
        ),
    )


DEFAULT_SCENARIO = build_default_scenario
"""既定の台本を作る関数（基準時刻を引数に取る）。"""


class MockBusinessSystem:
    """SQLite 上の業務システムモック。:class:`~sbp.integration.connectors.Connector` を満たす。

    実システム接続と同じ IF（``pull`` / ``stream`` / ``fingerprints``）を実装するため、
    上位層はモックと実システムを区別しない。
    """

    def __init__(self, *, table: str, db_path: Path | str = ":memory:") -> None:
        """Args:
        table: このコネクタが読むテーブル（1 コネクタ 1 テーブル）。
        db_path: SQLite のパス。既定はインメモリ。
        """
        if table not in _TABLE_TO_SOURCE:
            raise ValueError(f"unknown table: {table!r}; known: {sorted(_TABLE_TO_SOURCE)}")
        self._table = table
        self._connection = sqlite3.connect(str(db_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def __enter__(self) -> Self:
        """コンテキストマネージャとして使う。"""
        return self

    def __exit__(self, *_: object) -> None:
        """接続を閉じる。"""
        self.close()

    def close(self) -> None:
        """SQLite 接続を閉じる。"""
        self._connection.close()

    @property
    def source(self) -> str:
        """このコネクタが読む情報源の IRI。"""
        return _TABLE_TO_SOURCE[self._table]

    @property
    def table(self) -> str:
        """このコネクタが読むテーブル名。"""
        return self._table

    # ---- 台本の適用 ----

    def apply(self, scenario: Scenario) -> int:
        """台本を適用する（このコネクタのテーブルに関する手だけ）。

        Args:
            scenario: 適用する台本。

        Returns:
            適用した手の数。
        """
        applied = 0
        for step in scenario.steps:
            if step.table != self._table:
                continue
            self.upsert(step.values)
            applied += 1
        log.debug("mock.scenario_applied", scenario=scenario.name, table=self._table, steps=applied)
        return applied

    def add_column(self, name: str, sql_type: str = "TEXT") -> None:
        """テーブルに列を追加する（業務側のスキーマ変更を再現する）。

        業務システムのスキーマは基盤の都合とは無関係に変わる。ドリフト検知
        （設計書 §7.2）を検証するには、その変化を起こせる必要がある。

        Args:
            name: 追加する列名。
            sql_type: SQLite の型名。
        """
        self._connection.execute(f"ALTER TABLE {self._table} ADD COLUMN {name} {sql_type}")
        self._connection.commit()
        log.debug("mock.column_added", table=self._table, column=name, type=sql_type)

    def upsert(self, values: dict[str, Any]) -> None:
        """1 行を挿入または更新する。

        業務システムでの訂正（記録の書き換え）を再現するために使う。基盤側は訂正を
        「新しい主張」として受け取る（旧主張は二時制で保存される）。

        Args:
            values: 列名 → 値。``updated_at`` 省略時は ``recorded_at`` と同じ値を入れる。
        """
        payload = dict(values)
        payload.setdefault("updated_at", payload["recorded_at"])
        columns = ", ".join(payload)
        placeholders = ", ".join(f":{name}" for name in payload)
        keys = _PRIMARY_KEY[self._table]
        updates = ", ".join(f"{name} = :{name}" for name in payload if name not in keys)
        sql = (
            f"INSERT INTO {self._table} ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT({', '.join(keys)}) DO UPDATE SET {updates}"
        )
        self._connection.execute(sql, payload)
        self._connection.commit()

    # ---- Connector Protocol ----

    async def fingerprints(self) -> Sequence[SchemaFingerprint]:
        """このテーブルのスキーマ指紋を返す。"""
        rows = self._connection.execute(f"PRAGMA table_info({self._table})").fetchall()
        columns = tuple(sorted((row["name"], row["type"]) for row in rows))
        return (SchemaFingerprint(source=self.source, table=self._table, columns=columns),)

    async def pull(self, since: datetime | None = None) -> Sequence[RawRecord]:
        """``since`` 以降に更新された行を返す（``updated_at`` による差分取り込み）。

        Args:
            since: この時刻より後に更新された行だけを返す。``None`` なら全件。
        """
        if since is None:
            rows = self._connection.execute(
                f"SELECT * FROM {self._table} ORDER BY updated_at"
            ).fetchall()
        else:
            rows = self._connection.execute(
                f"SELECT * FROM {self._table} WHERE updated_at > ? ORDER BY updated_at",
                (_iso(since),),
            ).fetchall()
        return [self._to_raw(row) for row in rows]

    async def stream(self) -> AsyncIterator[RawRecord]:
        """現在の全行を順に返す（モックなので有限列）。

        実システムの ``stream`` は継続的に新しい行を返すが、モックでは台本の適用結果を
        1 周するだけにする。テストが終端を持てるようにするため。
        """
        for record in await self.pull():
            yield record

    def _to_raw(self, row: sqlite3.Row) -> RawRecord:
        """1 行を :class:`RawRecord` にする。

        値の解釈はしない。業務システムには実際に壊れた値（解釈できない日付など）が入りうるので、
        それを弾くのは写像の責務である（コネクタが先に落ちると、壊れた行の存在自体を
        上位層が知れなくなる）。``fetched_at`` は取得のメタデータなので、``updated_at`` が
        解釈できなければ現在時刻で代替する。
        """
        fields = dict(row)
        keys = _PRIMARY_KEY[self._table]
        external_id = "|".join(str(fields[k]) for k in keys)
        return RawRecord(
            source=self.source,
            table=self._table,
            external_id=external_id,
            fields=fields,
            fetched_at=_parse_or_now(fields.get("updated_at")),
        )


ConnectorKind = Literal["work_order", "operation_log", "spare_part_stock", "maintenance_plan"]
"""モックが提供するテーブルの種類。"""
