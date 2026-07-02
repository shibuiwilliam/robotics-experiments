"""S4: 資産台帳と、OR-full が起票した作業指示を世界グラフへ主張化。CQ/SHACL の対象。

資産（Asset --inSystem--> 系統）と、OR-full の同一化＋調停で異常と結論された資産への作業指示
（WorkOrder --forAsset--> / --appliesSop-->）を来歴・確信度・時刻付きで書き込む。
"""

from __future__ import annotations

from orx.common import iri
from orx.common.schemas import Claim, Term
from orx.common.seeding import SeedTree, deterministic_id
from orx.exp.suites.s4_inspection.model import S4Episode, S4World
from orx.exp.suites.s4_inspection.reference import anchor, reconcile
from orx.kg.world_graph import WorldGraph

_AGENT = iri.entity("agent", "inspection-planner")


def build_inspection_graph(world: S4World, episode: S4Episode, seeds: SeedTree) -> WorldGraph:
    rng = seeds.child("s4-graph").rng()
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

    for asset in world.assets:
        a_iri = iri.entity("asset", asset.asset_id)
        claim(a_iri, iri.RDF_TYPE, Term(kind="iri", value=iri.biz("Asset")), 0.0)
        claim(a_iri, iri.biz("inSystem"), Term(kind="literal", value=asset.system), 0.0)

    # OR-full の同一化＋調停 → 異常結論の資産へ作業指示を起票
    grouped: dict[str, list[tuple[bool, float]]] = {a.asset_id: [] for a in world.assets}
    for obs in episode.observations:
        a_id = anchor("OR-full", obs, episode.ledger)
        if a_id is not None:
            grouped[a_id].append((obs.anomaly_reading, obs.confidence))
    for asset in world.assets:
        if not reconcile("OR-full", grouped.get(asset.asset_id, [])):
            continue
        wo = iri.entity("workorder", asset.asset_id)
        a_iri = iri.entity("asset", asset.asset_id)
        claim(wo, iri.RDF_TYPE, Term(kind="iri", value=iri.biz("WorkOrder")), 1.0)
        claim(wo, iri.biz("forAsset"), Term(kind="iri", value=a_iri), 1.0)
        claim(wo, iri.biz("appliesSop"), Term(kind="literal", value=world.sop[asset.system]), 1.0)
    graph.refresh_current_graph(1.0)
    return graph
