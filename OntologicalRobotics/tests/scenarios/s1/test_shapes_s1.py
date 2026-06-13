"""S1 語彙のクラス固有 SHACL shape テスト（H-1）: 不適合フィクスチャを拒否する。"""

import pyshacl
import rdflib

from orx.common import iri
from orx.common.paths import shapes_dir

BIZ = rdflib.Namespace("https://orx.local/onto/biz#")


def shapes_graph() -> rdflib.Graph:
    g = rdflib.Graph()
    for path in sorted(shapes_dir().glob("*.ttl")):
        g.parse(path, format="turtle")
    return g


def validate(data: rdflib.Graph) -> bool:
    conforms, _, _ = pyshacl.validate(
        data_graph=data, shacl_graph=shapes_graph(), inference="none"
    )
    return bool(conforms)


def _lot(g: rdflib.Graph, lot_id: str) -> rdflib.URIRef:
    node = rdflib.URIRef(iri.entity("lot", lot_id))
    g.add((node, rdflib.RDF.type, BIZ.Lot))
    return node


def test_valid_recall_order_and_membership_conforms() -> None:
    g = rdflib.Graph()
    lot = _lot(g, "LOT-A")
    rec = rdflib.URIRef(iri.entity("recall", "RC-1"))
    g.add((rec, rdflib.RDF.type, BIZ.RecallOrder))
    g.add((rec, BIZ.targetsLot, lot))
    inst = rdflib.URIRef(iri.entity("shipinst", "SHIP-1"))
    g.add((inst, BIZ.memberOfLot, lot))
    assert validate(g)


def test_recall_order_without_targets_lot_rejected() -> None:
    g = rdflib.Graph()
    rec = rdflib.URIRef(iri.entity("recall", "RC-1"))
    g.add((rec, rdflib.RDF.type, BIZ.RecallOrder))  # targetsLot 欠落
    assert not validate(g)


def test_member_of_lot_pointing_to_non_lot_rejected() -> None:
    g = rdflib.Graph()
    inst = rdflib.URIRef(iri.entity("shipinst", "SHIP-1"))
    # 値が Lot 型として宣言されていない（識別子スレッドの不整合）
    g.add((inst, BIZ.memberOfLot, rdflib.URIRef(iri.entity("sku", "SKU-X"))))
    assert not validate(g)


def test_real_s1_graph_conforms() -> None:
    """実際の S1 グラフ（lot_claims 経由）がクラス固有 shape に適合する。"""
    from orx.business.db import LotData
    from orx.business.lifting import lot_claims
    from orx.common.seeding import SeedTree
    from orx.kg.world_graph import WorldGraph

    record_instructions = [
        {"instruction_id": "SHIP-2001", "order_id": "ORD-1001", "barcode": "BC-101"},
    ]

    class _Rec:
        instructions = record_instructions

    lot_data = LotData(
        members={"BC-101": "LOT-A"}, recall_lot="LOT-A", recall_id="RC-9001", recall_time=14.0
    )
    graph = WorldGraph()
    for claim in lot_claims(_Rec(), lot_data, SeedTree(7)):
        graph.assert_claim(claim)
    conforms, report = graph.validate_shacl()
    assert conforms, report
