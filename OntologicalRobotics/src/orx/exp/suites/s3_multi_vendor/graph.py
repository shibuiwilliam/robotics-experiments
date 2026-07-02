"""S3: 工程要求と能力契約、OR-full の語彙横断マッチを世界グラフへ主張化。

ProcessRequirement（SOPの工程要件）と各機体の Capability（能力契約）を来歴・確信度・時刻付きで
書き込み、OR-full が導出する matchesCapability（ベンダー非依存の適合）を主張する。CQ/SHACL の対象。
"""

from __future__ import annotations

from orx.common import iri
from orx.common.schemas import Claim, Term
from orx.common.seeding import SeedTree, deterministic_id
from orx.exp.suites.s3_multi_vendor.model import ProductSpec, S3World
from orx.kg.world_graph import WorldGraph

_AGENT = iri.entity("agent", "line-planner")


def _req_iri(product: ProductSpec) -> str:
    return iri.entity("processreq", product.name)


def _cap_iri(machine_id: str) -> str:
    return iri.entity("capability", f"{machine_id}-pick")


def build_capability_graph(world: S3World, seeds: SeedTree) -> WorldGraph:
    rng = seeds.child("s3-graph").rng()
    graph = WorldGraph()

    def claim(subject: str, predicate: str, obj: Term, t: float) -> None:
        graph.assert_claim(
            Claim(
                claim_id=deterministic_id(rng),
                subject=subject,
                predicate=predicate,
                object=obj,
                asserted_by=_AGENT,
                confidence=1.0,
                observed_at=t,
                valid_until=None,
            )
        )

    # 能力契約（各機体）: Capability --forRobot--> robot, declaredPayloadKg
    for m in world.machines:
        cap = _cap_iri(m.machine_id)
        claim(cap, iri.RDF_TYPE, Term(kind="iri", value=iri.cap("Capability")), 0.0)
        claim(
            cap, iri.cap("forRobot"), Term(kind="iri", value=iri.entity("robot", m.machine_id)), 0.0
        )
        claim(
            cap,
            iri.cap("declaredPayloadKg"),
            Term(kind="literal", value=repr(m.declared_payload_kg), datatype=iri.XSD_DOUBLE),
            0.0,
        )

    # 工程要求（SOP・品種ごと）
    for product in world.products.values():
        req = _req_iri(product)
        claim(req, iri.RDF_TYPE, Term(kind="iri", value=iri.cap("ProcessRequirement")), 0.0)
        claim(
            req,
            iri.cap("requiresPayloadKg"),
            Term(kind="literal", value=repr(product.weight_kg), datatype=iri.XSD_DOUBLE),
            0.0,
        )
        claim(req, iri.cap("requiresMaterial"), Term(kind="literal", value=product.material), 0.0)

    # OR-full の語彙横断マッチ: 工程要求 --matchesCapability--> 実現可能な能力契約（全ベンダー）
    for product in world.products.values():
        req = _req_iri(product)
        for m in world.machines:
            if product.weight_kg <= m.declared_payload_kg:
                claim(
                    req,
                    iri.cap("matchesCapability"),
                    Term(kind="iri", value=_cap_iri(m.machine_id)),
                    1.0,
                )
    graph.refresh_current_graph(1.0)
    return graph
