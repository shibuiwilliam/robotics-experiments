"""S2: 導出された汚染を世界グラフへ主張化（CQ・SHACL の対象）。

OR-full の推移閉包（eval_time）を `orx-st:possiblyContaminatedBy` 主張として、接触を
`orx-st:ContactEvent` 主張として、来歴・確信度・時刻付きで世界グラフへ書き込む。
"""

from __future__ import annotations

from orx.common import iri
from orx.common.schemas import Claim, Term
from orx.common.seeding import SeedTree, deterministic_id
from orx.exp.suites.s2_allergen.model import S2Episode
from orx.kg.world_graph import WorldGraph
from orx.oracle.scenarios.s2 import contamination_closure

_AGENT = iri.entity("agent", "contamination-reasoner")


def build_contamination_graph(
    episode: S2Episode, allergens: list[str], seeds: SeedTree
) -> WorldGraph:
    rng = seeds.child("s2-graph").rng()
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

    for a in allergens:
        claim(
            iri.entity("allergen", a),
            iri.RDF_TYPE,
            Term(kind="iri", value=iri.biz("AllergenClass")),
            0.0,
        )
    for i, c in enumerate(episode.contacts):
        ce = iri.entity("contact", str(i))
        claim(ce, iri.RDF_TYPE, Term(kind="iri", value=iri.st("ContactEvent")), c.sim_time)
        claim(
            ce,
            iri.st("contactParty"),
            Term(kind="iri", value=iri.entity("object", c.a)),
            c.sim_time,
        )
        claim(
            ce,
            iri.st("contactParty"),
            Term(kind="iri", value=iri.entity("object", c.b)),
            c.sim_time,
        )
    state = contamination_closure(
        episode.intrinsic, episode.contacts, episode.cleanings, episode.eval_time
    )
    for entity, alls in state.carried.items():
        for a in alls:
            claim(
                iri.entity("object", entity),
                iri.st("possiblyContaminatedBy"),
                Term(kind="iri", value=iri.entity("allergen", a)),
                episode.eval_time,
            )
    graph.refresh_current_graph(episode.eval_time)
    return graph
