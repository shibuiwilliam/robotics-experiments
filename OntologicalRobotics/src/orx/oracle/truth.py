"""真理グラフ生成器（C8）— シム真値から完全な真値ABoxスナップショットを作る。

oracle は sim 真値と common のみに依存し、ORコア（anchoring/kg/agent）を
import しない（import-linter で強制）。

D2 述語集合 v0: rdf:type / orx-st:inZone / orx-upper:hasIdentifier。
連続位置はトリプルではなく RMSE で別評価する。新述語を追加したら
tasks/competency_questions/ と本リストの両方を更新すること（CLAUDE.md §9）。
"""

from __future__ import annotations

from orx.common import iri
from orx.common.schemas import StateSnapshot, TripleRecord, TruthState

FIDELITY_PREDICATES: tuple[str, ...] = (
    iri.RDF_TYPE,
    iri.st("inZone"),
    iri.upper("hasIdentifier"),
)


def truth_entity(object_id: str) -> str:
    """真値個体のIRI（世界グラフの個体IRIとは別空間）。"""
    return iri.entity("truth-object", object_id)


def truth_snapshot(state: TruthState) -> StateSnapshot:
    """真値状態 → 真値ABoxスナップショット（D2述語のみ）。"""
    triples: list[TripleRecord] = []
    positions: dict[str, tuple[float, float, float]] = {}
    for obj in state.objects:
        subject = truth_entity(obj.object_id)
        positions[subject] = obj.position
        triples.append(
            TripleRecord(
                subject=subject,
                predicate=iri.RDF_TYPE,
                object=f"<{iri.upper('Box')}>",
            )
        )
        if obj.zone is not None:
            triples.append(
                TripleRecord(
                    subject=subject,
                    predicate=iri.st("inZone"),
                    object=f"<{iri.entity('zone', obj.zone)}>",
                )
            )
        if obj.barcode is not None:
            triples.append(
                TripleRecord(
                    subject=subject,
                    predicate=iri.upper("hasIdentifier"),
                    object=f'"{obj.barcode}"',
                )
            )
    return StateSnapshot(sim_time=state.sim_time, triples=triples, positions=positions)
