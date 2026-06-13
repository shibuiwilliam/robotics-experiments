"""S6: 規制規則とOR-fullの割当を世界グラフへ主張化（CQ・SHACL の対象）。

規制オントロジー（RegulatedClass --disposalRoute--> DisposalLane）と、OR-full が各物体に
割り当てたレーン（routedTo）を来歴・確信度・時刻付きで書き込む。
"""

from __future__ import annotations

from orx.common import iri
from orx.common.schemas import Claim, Term
from orx.common.seeding import SeedTree, deterministic_id
from orx.exp.suites.s6_recycling.grounding import class_prototypes
from orx.exp.suites.s6_recycling.model import S6Episode, S6World
from orx.exp.suites.s6_recycling.reference import assign_lane
from orx.kg.world_graph import WorldGraph
from orx.oracle.scenarios.s6 import ESCALATE

_AGENT = iri.entity("agent", "recycling-router")


def build_routing_graph(
    world: S6World, episode: S6Episode, seeds: SeedTree, confidence_threshold: float
) -> WorldGraph:
    rng = seeds.child("s6-graph").rng()
    protos = class_prototypes(world.classes, world.embedding_dim)
    graph = WorldGraph()

    def claim(subject: str, predicate: str, obj: Term, t: float) -> None:
        graph.assert_claim(
            Claim(
                claim_id=deterministic_id(rng), subject=subject, predicate=predicate,
                object=obj, asserted_by=_AGENT, confidence=1.0, observed_at=t, valid_until=None,
            )
        )

    # 規制規則（記号側の知識）: RegulatedClass --disposalRoute--> DisposalLane
    for cls, lane in world.disposal_route.items():
        c_iri = iri.entity("regclass", cls)
        claim(c_iri, iri.RDF_TYPE, Term(kind="iri", value=iri.biz("RegulatedClass")), 0.0)
        lane_iri = iri.entity("lane", lane)
        claim(lane_iri, iri.RDF_TYPE, Term(kind="iri", value=iri.st("DisposalLane")), 0.0)
        claim(c_iri, iri.biz("disposalRoute"), Term(kind="iri", value=lane_iri), 0.0)
    # OR-full の割当（routedTo）
    for obj in episode.objects:
        lane, _conf = assign_lane("OR-full", obj, world, protos, confidence_threshold)
        if lane == ESCALATE:
            continue
        claim(
            iri.entity("object", obj.obj_id), iri.biz("routedTo"),
            Term(kind="iri", value=iri.entity("lane", lane)), 1.0,
        )
    graph.refresh_current_graph(1.0)
    return graph
