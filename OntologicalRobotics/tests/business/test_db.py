"""C3 模擬WMSのテスト: 決定性・識別子スレッド整合・読み取り専用。"""

from pathlib import Path

import pytest

from orx.business.db import BusinessDB, generate_wms
from orx.business.lifting import wms_claims
from orx.common import iri
from orx.common.config import WorldConfig, load_config
from orx.common.paths import repo_root
from orx.common.seeding import SeedTree

WORLD = repo_root() / "configs" / "world" / "t2_warehouse.yaml"


@pytest.fixture(scope="module")
def world() -> WorldConfig:
    return load_config(WORLD, WorldConfig)


def test_wms_deterministic(world: WorldConfig, tmp_path: Path) -> None:
    r1 = generate_wms(world, SeedTree(201), tmp_path / "a.sqlite")
    r2 = generate_wms(world, SeedTree(201), tmp_path / "b.sqlite")
    assert r1 == r2
    r3 = generate_wms(world, SeedTree(202), tmp_path / "c.sqlite")
    assert r1 != r3


def test_identity_thread_consistency(world: WorldConfig, tmp_path: Path) -> None:
    record = generate_wms(world, SeedTree(201), tmp_path / "wms.sqlite")
    barcodes = {b.barcode for b in world.boxes if b.barcode}
    # 全出荷指示のバーコードは実在する箱を指す
    assert {i["barcode"] for i in record.instructions} <= barcodes
    # 各バーコード付き箱にちょうど1指示
    assert len(record.instructions) == len(barcodes)
    # 物理個体に紐づかない open 受注が存在する（負例）
    linked = {i["order_id"] for i in record.instructions}
    open_orders = [o for o in record.orders if o["order_id"] not in linked]
    assert open_orders and all(o["status"] == "open" for o in open_orders)


def test_db_readonly_and_query(world: WorldConfig, tmp_path: Path) -> None:
    generate_wms(world, SeedTree(201), tmp_path / "wms.sqlite")
    db = BusinessDB(tmp_path / "wms.sqlite")
    rows = db.query("SELECT COUNT(*) AS n FROM orders")
    assert rows[0]["n"] >= 8
    csv = db.dump_csv()
    assert "orders" in csv and "shipping_instructions" in csv


def test_wms_claims_have_provenance(world: WorldConfig, tmp_path: Path) -> None:
    record = generate_wms(world, SeedTree(201), tmp_path / "wms.sqlite")
    claims = wms_claims(record, SeedTree(201))
    assert claims
    assert all(c.asserted_by == iri.entity("agent", "wms") for c in claims)
    assert all(c.confidence == 1.0 for c in claims)
    # 識別子スレッド: hasBarcode 主張が箱のバーコードと一致
    barcode_claims = {c.object.value for c in claims if c.predicate == iri.biz("hasBarcode")}
    assert barcode_claims == {b.barcode for b in world.boxes if b.barcode}
    # 決定的
    assert claims == wms_claims(record, SeedTree(201))
