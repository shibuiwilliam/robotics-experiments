"""S7: 入居者台帳（所有・居室・最終目撃）を世界グラフへ主張化。CQ/SHACL の対象。

所有(orx-biz:owns)・居室(assignedRoom)・最終目撃ゾーン(orx-st:lastSeenZone)を来歴・確信度・
時刻付きで書き込む。所有は観測不可能な情報的関係であり、台帳にのみ存在する（H6）。
"""

from __future__ import annotations

from orx.common import iri
from orx.common.schemas import Claim, Term
from orx.common.seeding import SeedTree, deterministic_id
from orx.exp.suites.s7_ownership.model import S7World
from orx.kg.world_graph import WorldGraph

_AGENT = iri.entity("agent", "care-ledger")


def _item_iri(resident: str, item_type: str) -> str:
    return iri.entity("personalitem", f"{resident}-{item_type}")


def build_ownership_graph(world: S7World, seeds: SeedTree) -> WorldGraph:
    rng = seeds.child("s7-graph").rng()
    graph = WorldGraph()

    def claim(subject: str, predicate: str, obj: Term, t: float) -> None:
        graph.assert_claim(
            Claim(
                claim_id=deterministic_id(rng), subject=subject, predicate=predicate,
                object=obj, asserted_by=_AGENT, confidence=1.0, observed_at=t, valid_until=None,
            )
        )

    for r in world.residents:
        r_iri = iri.entity("resident", r)
        item = _item_iri(r, world.item_type)
        claim(r_iri, iri.RDF_TYPE, Term(kind="iri", value=iri.biz("Resident")), 0.0)
        claim(item, iri.RDF_TYPE, Term(kind="iri", value=iri.biz("PersonalItem")), 0.0)
        claim(r_iri, iri.biz("owns"), Term(kind="iri", value=item), 0.0)
        claim(r_iri, iri.biz("assignedRoom"), Term(kind="literal", value=world.rooms[r]), 0.0)
        claim(item, iri.st("lastSeenZone"),
              Term(kind="literal", value=world.resident_zone[r]), 0.0)
    graph.refresh_current_graph(1.0)
    return graph
