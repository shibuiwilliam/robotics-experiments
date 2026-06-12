"""C6 世界グラフのテスト: 書込・調停・失効・物質化・SHACL・sameAs禁止。

ストアはテスト毎にフィクスチャで生成する（CLAUDE.md §9: 状態漏れ防止）。
"""

import pytest

from orx.common import iri
from orx.common.schemas import Claim, Term
from orx.kg.world_graph import CURRENT_GRAPH, WorldGraph

AGENT = iri.entity("agent", "anchoring-arm_a")
BOX = iri.entity("object", "e1")
ZONE_A = iri.entity("zone", "shelf_a")
ZONE_B = iri.entity("zone", "dock")


def claim_inzone(
    cid: str, zone: str, t: float, ttl: float | None = 5.0, conf: float = 0.9
) -> Claim:
    return Claim(
        claim_id=cid,
        subject=BOX,
        predicate=iri.st("inZone"),
        object=Term(kind="iri", value=zone),
        asserted_by=AGENT,
        confidence=conf,
        observed_at=t,
        valid_until=None if ttl is None else t + ttl,
    )


@pytest.fixture()
def graph() -> WorldGraph:
    return WorldGraph()


def test_assert_and_query_roundtrip(graph: WorldGraph) -> None:
    graph.assert_claim(claim_inzone("c1", ZONE_A, 1.0))
    rows = graph.query(
        f"SELECT ?z WHERE {{ GRAPH ?g {{ <{BOX}> <{iri.st('inZone')}> ?z }} }}"
    )
    assert any(r["z"] == ZONE_A for r in rows)


def test_metadata_written_to_meta_graph(graph: WorldGraph) -> None:
    graph.assert_claim(claim_inzone("c1", ZONE_A, 1.0))
    rows = graph.query(
        "SELECT ?conf ?agent WHERE { GRAPH <https://orx.local/id/graph/meta> {"
        f" ?claim <{iri.prov('confidence')}> ?conf ;"
        f" <{iri.prov('assertedBy')}> ?agent }} }}"
    )
    assert rows and rows[0]["conf"] == "0.9"
    assert rows[0]["agent"] == AGENT


def test_functional_arbitration_latest_wins(graph: WorldGraph) -> None:
    graph.assert_claim(claim_inzone("c1", ZONE_A, 1.0))
    graph.assert_claim(claim_inzone("c2", ZONE_B, 2.0))
    snap = graph.snapshot(at_time=2.5)
    zones = [t for t in snap.triples if t.predicate == iri.st("inZone")]
    assert len(zones) == 1
    assert zones[0].object == f"<{ZONE_B}>"


def test_expired_claims_excluded(graph: WorldGraph) -> None:
    graph.assert_claim(claim_inzone("c1", ZONE_A, 1.0, ttl=2.0))
    assert graph.snapshot(at_time=1.5).triples  # 期限内
    assert not graph.snapshot(at_time=4.0).triples  # 失効


def test_staleness_rate(graph: WorldGraph) -> None:
    graph.assert_claim(claim_inzone("c1", ZONE_A, 1.0, ttl=2.0))
    assert graph.staleness_rate(at_time=1.5) == 0.0
    assert graph.staleness_rate(at_time=10.0) == 1.0
    # 後続主張で置換されれば陳腐でない
    graph.assert_claim(claim_inzone("c2", ZONE_B, 9.5, ttl=5.0))
    assert graph.staleness_rate(at_time=10.0) == 0.0


def test_refresh_current_graph_materializes(graph: WorldGraph) -> None:
    graph.assert_claim(claim_inzone("c1", ZONE_A, 1.0))
    graph.assert_claim(claim_inzone("c2", ZONE_B, 2.0))
    graph.refresh_current_graph(at_time=2.5)
    rows = graph.query(
        f"SELECT ?z WHERE {{ GRAPH <{CURRENT_GRAPH}> {{ <{BOX}> <{iri.st('inZone')}> ?z }} }}"
    )
    assert [r["z"] for r in rows] == [ZONE_B]


def test_same_as_write_rejected(graph: WorldGraph) -> None:
    bad = Claim(
        claim_id="c1",
        subject=BOX,
        predicate="http://www.w3.org/2002/07/owl#sameAs",
        object=Term(kind="iri", value=ZONE_A),
        asserted_by=AGENT,
        confidence=1.0,
        observed_at=0.0,
    )
    with pytest.raises(ValueError, match="sameAs"):
        graph.assert_claim(bad)


def test_position_snapshot(graph: WorldGraph) -> None:
    for cid, pred, val in [("px", "posX", 0.1), ("py", "posY", 0.2), ("pz", "posZ", 0.3)]:
        graph.assert_claim(
            Claim(
                claim_id=cid,
                subject=BOX,
                predicate=iri.st(pred),
                object=Term(kind="literal", value=str(val), datatype=iri.XSD_DOUBLE),
                asserted_by=AGENT,
                confidence=0.95,
                observed_at=1.0,
                valid_until=6.0,
            )
        )
    snap = graph.snapshot(at_time=2.0)
    assert snap.positions[BOX] == (0.1, 0.2, 0.3)


def test_shacl_conformance_of_write_path(graph: WorldGraph) -> None:
    graph.assert_claim(claim_inzone("c1", ZONE_A, 1.0))
    conforms, report = graph.validate_shacl()
    assert conforms, report
