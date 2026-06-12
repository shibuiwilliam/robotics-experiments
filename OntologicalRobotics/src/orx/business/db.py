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
