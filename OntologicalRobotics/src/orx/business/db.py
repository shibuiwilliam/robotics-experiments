"""C3 — 模擬WMS（SQLite）。受注 / SKU / 出荷指示。

アイデンティティ・スレッドの種データ: 受注 → 出荷指示 → バーコード →（物理個体）。
バーコードは世界コンフィグの箱と整合させ、シード駆動で決定的に生成する。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from orx.common.config import WorldConfig
from orx.common.schemas import StrictModel
from orx.common.seeding import SeedTree

_SKU_CATALOG: list[tuple[str, str, float, int]] = [
    # (sku, name, weight_kg, fragile)
    ("SKU-GLS", "glass panel", 1.2, 1),
    ("SKU-MTR", "servo motor", 3.5, 0),
    ("SKU-CBL", "cable drum", 7.0, 0),
    ("SKU-SNS", "lidar sensor", 0.8, 1),
    ("SKU-BRK", "brake unit", 4.2, 0),
    ("SKU-PMP", "vacuum pump", 5.5, 0),
]

_DDL = """
CREATE TABLE skus (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    weight_kg REAL NOT NULL,
    fragile INTEGER NOT NULL
);
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    sku TEXT NOT NULL REFERENCES skus(sku),
    quantity INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'allocated', 'shipped')),
    destination TEXT NOT NULL
);
CREATE TABLE shipping_instructions (
    instruction_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    barcode TEXT NOT NULL
);
"""

_DESTINATIONS = ["osaka", "nagoya", "sendai", "fukuoka"]


class WmsRecord(StrictModel):
    """生成されたWMSの論理内容（オントロジー写像・真値導出が使う）。"""

    orders: list[dict[str, str | int | float]]
    skus: list[dict[str, str | int | float]]
    instructions: list[dict[str, str]]


def generate_wms(world: WorldConfig, seeds: SeedTree, db_path: Path) -> WmsRecord:
    """世界の箱（バーコード付き）に整合するWMSを決定的に生成する。

    - バーコード付きの各箱 → 1出荷指示 → 1受注（status=allocated）
    - 加えて物理個体に対応しない受注（status=open）を2件（負例用）
    """
    rng = seeds.child("wms").rng()
    barcoded = [b for b in world.boxes if b.barcode is not None]

    skus = [
        {"sku": s, "name": n, "weight_kg": w, "fragile": f}
        for s, n, w, f in _SKU_CATALOG
    ]
    orders: list[dict[str, str | int | float]] = []
    instructions: list[dict[str, str]] = []
    for i, box in enumerate(barcoded):
        order_id = f"ORD-{1001 + i}"
        sku = _SKU_CATALOG[int(rng.integers(0, len(_SKU_CATALOG)))][0]
        orders.append(
            {
                "order_id": order_id,
                "sku": sku,
                "quantity": int(rng.integers(1, 4)),
                "status": "allocated",
                "destination": _DESTINATIONS[int(rng.integers(0, len(_DESTINATIONS)))],
            }
        )
        instructions.append(
            {
                "instruction_id": f"SHIP-{2001 + i}",
                "order_id": order_id,
                "barcode": str(box.barcode),
            }
        )
    for j in range(2):  # 物理個体に対応しない未割当受注
        order_id = f"ORD-{1901 + j}"
        sku = _SKU_CATALOG[int(rng.integers(0, len(_SKU_CATALOG)))][0]
        orders.append(
            {
                "order_id": order_id,
                "sku": sku,
                "quantity": int(rng.integers(1, 4)),
                "status": "open",
                "destination": _DESTINATIONS[int(rng.integers(0, len(_DESTINATIONS)))],
            }
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_DDL)
        conn.executemany(
            "INSERT INTO skus VALUES (:sku, :name, :weight_kg, :fragile)", skus
        )
        conn.executemany(
            "INSERT INTO orders VALUES (:order_id, :sku, :quantity, :status, :destination)",
            orders,
        )
        conn.executemany(
            "INSERT INTO shipping_instructions VALUES (:instruction_id, :order_id, :barcode)",
            instructions,
        )
        conn.commit()
    finally:
        conn.close()
    return WmsRecord(orders=orders, skus=skus, instructions=instructions)


class LotData(StrictModel):
    """S1 ロット回収のロット/回収データ（業務真値の種）。"""

    members: dict[str, str]  # barcode -> lot_id
    recall_lot: str
    recall_id: str
    recall_time: float


_LOT_DDL = """
CREATE TABLE lots (lot_id TEXT PRIMARY KEY);
CREATE TABLE lot_members (
    barcode TEXT PRIMARY KEY,
    lot_id TEXT NOT NULL REFERENCES lots(lot_id)
);
CREATE TABLE recall_orders (
    recall_id TEXT PRIMARY KEY,
    lot_id TEXT NOT NULL REFERENCES lots(lot_id),
    recall_time REAL NOT NULL
);
"""


def generate_lot_tables(
    world: WorldConfig, db_path: Path, recall_lot: str, recall_time: float
) -> LotData:
    """世界コンフィグの box.lot から lot/lot_members/recall_orders を生成する。

    既存 WMS sqlite（generate_wms 後）にテーブルを追加する。lot↔個装(barcode) の
    参照連鎖がここで定義され、S1 の回収逆引きの業務真値となる。
    """
    members = {b.barcode: b.lot for b in world.boxes if b.barcode and b.lot}
    if recall_lot not in set(members.values()):
        raise ValueError(f"回収ロット {recall_lot!r} に属する個装がありません")
    lots = sorted(set(members.values()))
    recall_id = "RC-9001"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_LOT_DDL)
        conn.executemany("INSERT INTO lots VALUES (:lot_id)", [{"lot_id": v} for v in lots])
        conn.executemany(
            "INSERT INTO lot_members VALUES (:barcode, :lot_id)",
            [{"barcode": bc, "lot_id": lot} for bc, lot in sorted(members.items())],
        )
        conn.execute(
            "INSERT INTO recall_orders VALUES (?, ?, ?)",
            (recall_id, recall_lot, recall_time),
        )
        conn.commit()
    finally:
        conn.close()
    return LotData(
        members=members, recall_lot=recall_lot, recall_id=recall_id, recall_time=recall_time
    )


class BusinessDB:
    """読み取り専用のWMSアクセス（エージェントツール・真値導出が使う）。"""

    def __init__(self, db_path: Path) -> None:
        if not db_path.exists():
            raise FileNotFoundError(f"WMS DBがありません: {db_path}")
        self._uri = f"file:{db_path}?mode=ro"

    def query(self, sql: str) -> list[dict[str, object]]:
        conn = sqlite3.connect(self._uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def dump_csv(self) -> str:
        """B0ベースライン用: 全テーブルのCSVダンプ。"""
        out: list[str] = []
        for table in ("skus", "orders", "shipping_instructions"):
            rows = self.query(f"SELECT * FROM {table}")  # noqa: S608 - 固定テーブル名
            out.append(f"## {table}")
            if rows:
                cols = list(rows[0].keys())
                out.append(",".join(cols))
                for r in rows:
                    out.append(",".join(str(r[c]) for c in cols))
            out.append("")
        return "\n".join(out)
