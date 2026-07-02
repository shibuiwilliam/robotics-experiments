"""K0 — アクション書戻し Claim の構築と SHACL 適合。

effect/custody/action-execution の Claim を WorldGraph に assert_claim で書き、
ClaimShape（来歴・確信度・時刻）＋ ActionExecutionShape/CustodyStepShape に適合することを検証する。
"""

from __future__ import annotations

from orx.common import iri
from orx.common.seeding import SeedTree, deterministic_id
from orx.kg.action_claims import (
    action_execution_claims,
    custody_step_claims,
    effect_claim,
)
from orx.kg.world_graph import WorldGraph


def _idgen(seed: int = 7):
    rng = SeedTree(seed).child("act-claims").rng()
    return lambda: deterministic_id(rng)


def test_effect_and_audit_claims_conform_shacl() -> None:
    new_id = _idgen()
    graph = WorldGraph()
    agent = iri.entity("agent", "r1")
    obj = iri.entity("object", "BC-X")
    robot = iri.entity("robot", "r1")

    graph.assert_claim(effect_claim(new_id, obj, "dock", asserted_by=agent, observed_at=2.0))
    for c in custody_step_claims(
        new_id, obj, "dock", step_index=0, asserted_by=agent, observed_at=2.0
    ):
        graph.assert_claim(c)
    _exec_iri, claims = action_execution_claims(
        new_id, robot, obj, "dock", status="applied", observed_at=2.0, asserted_by=agent
    )
    for c in claims:
        graph.assert_claim(c)

    conforms, report = graph.validate_shacl()
    assert conforms, report


def test_effect_claim_is_functional_inzone() -> None:
    """effect は inZone（関数的）— 後続 inZone 再主張がアンドゥとして上書きする。"""
    new_id = _idgen()
    graph = WorldGraph()
    agent = iri.entity("agent", "r1")
    obj = iri.entity("object", "BC-X")
    graph.assert_claim(effect_claim(new_id, obj, "dock", asserted_by=agent, observed_at=2.0))
    graph.assert_claim(effect_claim(new_id, obj, "bench", asserted_by=agent, observed_at=3.0))
    graph.refresh_current_graph(4.0)
    rows = graph.query(
        f"SELECT ?z WHERE {{ GRAPH <{iri_current()}> {{ <{obj}> <{iri.st('inZone')}> ?z }} }}"
    )
    zones = {r["z"] for r in rows}
    assert zones == {iri.entity("zone", "bench")}  # 最新（補償）が勝つ


def test_compensation_links_to_original() -> None:
    new_id = _idgen()
    graph = WorldGraph()
    agent = iri.entity("agent", "r1")
    obj = iri.entity("object", "BC-X")
    robot = iri.entity("robot", "r1")
    orig_iri, orig = action_execution_claims(
        new_id, robot, obj, "dock", status="applied", observed_at=2.0, asserted_by=agent
    )
    for c in orig:
        graph.assert_claim(c)
    _comp_iri, comp = action_execution_claims(
        new_id,
        robot,
        obj,
        "bench",
        status="applied",
        observed_at=3.0,
        asserted_by=agent,
        compensates_iri=orig_iri,
    )
    for c in comp:
        graph.assert_claim(c)
    conforms, report = graph.validate_shacl()
    assert conforms, report


def iri_current() -> str:
    from orx.kg.world_graph import CURRENT_GRAPH

    return CURRENT_GRAPH
