"""S5: 規範と OR-full の custody連鎖を世界グラフへ主張化。CQ/SHACL の対象。

禁止規範（Prohibition --appliesToItemClass/--forbidsZoneClass）と、OR-full の各搬送の
custody連鎖（CustodyStep --custodyOf/--atZone/--stepIndex）を来歴・確信度・時刻付きで書き込む。
"""

from __future__ import annotations

from orx.common import iri
from orx.common.schemas import Claim, Term
from orx.common.seeding import SeedTree, deterministic_id
from orx.exp.suites.s5_hospital.model import S5World
from orx.exp.suites.s5_hospital.reference import plan_route, record_custody
from orx.kg.world_graph import WorldGraph

_AGENT = iri.entity("agent", "transport-planner")
_XSD_INT = "http://www.w3.org/2001/XMLSchema#integer"


def build_custody_graph(world: S5World, seeds: SeedTree) -> WorldGraph:
    rng = seeds.child("s5-graph").rng()
    graph = WorldGraph()

    def claim(subject: str, predicate: str, obj: Term, t: float) -> None:
        graph.assert_claim(
            Claim(
                claim_id=deterministic_id(rng), subject=subject, predicate=predicate,
                object=obj, asserted_by=_AGENT, confidence=1.0, observed_at=t, valid_until=None,
            )
        )

    # 禁止規範（記号側の知識）
    for i, n in enumerate(world.norms):
        n_iri = iri.entity("norm", f"prohibition-{i}")
        claim(n_iri, iri.RDF_TYPE, Term(kind="iri", value=iri.norm("Prohibition")), 0.0)
        claim(n_iri, iri.norm("appliesToItemClass"), Term(kind="literal", value=n.item_class), 0.0)
        claim(n_iri, iri.norm("forbidsZoneClass"),
              Term(kind="literal", value=n.forbidden_zone_class), 0.0)

    # OR-full の custody連鎖（全通行を記録）
    for tr in world.transports:
        route = plan_route("OR-full", tr, world)
        recorded = record_custody("OR-full", route)
        item_iri = iri.entity("transportitem", tr.transport_id)
        for idx, zone in enumerate(recorded):
            step = iri.entity("custodystep", f"{tr.transport_id}-{idx}")
            claim(step, iri.RDF_TYPE, Term(kind="iri", value=iri.norm("CustodyStep")), float(idx))
            claim(step, iri.norm("custodyOf"), Term(kind="iri", value=item_iri), float(idx))
            claim(step, iri.norm("atZone"), Term(kind="literal", value=zone), float(idx))
            claim(step, iri.norm("stepIndex"),
                  Term(kind="literal", value=str(idx), datatype=_XSD_INT), float(idx))
    graph.refresh_current_graph(float(len(world.transports) + 1))
    return graph
