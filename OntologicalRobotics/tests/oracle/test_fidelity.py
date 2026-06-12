"""忠実度メトリクスのテスト（手計算と一致する合成ケース）。"""

import math

from orx.common import iri
from orx.common.schemas import AnchorObservation, StateSnapshot, TripleRecord
from orx.oracle.fidelity import (
    fidelity_report,
    majority_alignment,
    pairwise_identity_prf,
    position_rmse,
    transition_metrics,
    triple_prf,
    zone_transitions,
)
from orx.oracle.truth import truth_entity

E1 = "https://orx.local/id/object/e1"
E2 = "https://orx.local/id/object/e2"


def obs(entity: str, true_id: str, t: float = 1.0) -> AnchorObservation:
    return AnchorObservation(entity_iri=entity, true_object_id=true_id, sim_time=t)


def test_majority_alignment_and_tie_determinism() -> None:
    observations = [obs(E1, "b1"), obs(E1, "b1"), obs(E1, "b2"), obs(E2, "a"), obs(E2, "b")]
    alignment = majority_alignment(observations)
    assert alignment[E1] == "b1"
    assert alignment[E2] == "a"  # 同数タイは辞書順最小


def test_pairwise_identity_hand_computed() -> None:
    # E1: [a, a, b] / E2: [c] → ペア: (a,a)=TP, (a,b)x2=FP, FN=0
    observations = [obs(E1, "a"), obs(E1, "a"), obs(E1, "b"), obs(E2, "c")]
    p, r, f1 = pairwise_identity_prf(observations)
    assert p == 1 / 3
    assert r == 1.0
    assert abs(f1 - 0.5) < 1e-9


def zone_triple(subject: str, zone: str) -> TripleRecord:
    return TripleRecord(
        subject=subject, predicate=iri.st("inZone"), object=f"<{iri.entity('zone', zone)}>"
    )


def type_triple(subject: str) -> TripleRecord:
    return TripleRecord(subject=subject, predicate=iri.RDF_TYPE, object=f"<{iri.upper('Box')}>")


def test_triple_prf_hand_computed() -> None:
    alignment = {E1: "b1"}
    world = [StateSnapshot(sim_time=1.0, triples=[type_triple(E1), zone_triple(E1, "dock")])]
    truth = [
        StateSnapshot(
            sim_time=1.0,
            triples=[
                type_triple(truth_entity("b1")),
                zone_triple(truth_entity("b1"), "shelf_a"),
                type_triple(truth_entity("b2")),
            ],
        )
    ]
    p, r, f1 = triple_prf(world, truth, alignment)
    # world: {type b1 (TP), inZone dock (FP)} truth残: {inZone shelf_a, type b2} = FN 2
    assert p == 0.5
    assert r == 1 / 3
    assert abs(f1 - 0.4) < 1e-9


def test_position_rmse_hand_computed() -> None:
    alignment = {E1: "b1"}
    world = [StateSnapshot(sim_time=1.0, triples=[], positions={E1: (1.0, 0.0, 0.0)})]
    truth = [
        StateSnapshot(
            sim_time=1.0, triples=[], positions={truth_entity("b1"): (0.0, 0.0, 0.0)}
        )
    ]
    assert abs(position_rmse(world, truth, alignment) - 1.0) < 1e-9


def test_zone_transitions_and_metrics() -> None:
    t1 = StateSnapshot(sim_time=1.0, triples=[zone_triple("x", "shelf_a")])
    t2 = StateSnapshot(sim_time=2.0, triples=[zone_triple("x", "shelf_a")])
    t3 = StateSnapshot(sim_time=3.0, triples=[zone_triple("x", "dock")])
    transitions = zone_transitions([t1, t2, t3])
    assert len(transitions) == 1
    assert transitions[0].to_zone == iri.entity("zone", "dock")
    assert transitions[0].sim_time == 3.0

    delay, miss = transition_metrics(transitions, transitions)
    assert delay == 0.0 and miss == 0.0
    delay, miss = transition_metrics([], transitions)
    assert delay is None and miss == 1.0


def test_fidelity_report_perfect_case() -> None:
    alignment_obs = [obs(E1, "b1", 1.0), obs(E1, "b1", 2.0)]
    world = [
        StateSnapshot(
            sim_time=1.0,
            triples=[type_triple(E1), zone_triple(E1, "shelf_a")],
            positions={E1: (0.0, 0.0, 0.0)},
        )
    ]
    truth = [
        StateSnapshot(
            sim_time=1.0,
            triples=[type_triple(truth_entity("b1")), zone_triple(truth_entity("b1"), "shelf_a")],
            positions={truth_entity("b1"): (0.0, 0.0, 0.0)},
        )
    ]
    report = fidelity_report(world, truth, alignment_obs, staleness_rate=0.0)
    assert report.triple_f1 == 1.0
    assert report.identity_f1 == 1.0
    assert report.position_rmse == 0.0
    assert report.transition_miss_rate == 0.0


def test_unaligned_world_entity_counts_as_fp() -> None:
    world = [StateSnapshot(sim_time=1.0, triples=[type_triple(E2)])]
    truth = [StateSnapshot(sim_time=1.0, triples=[type_triple(truth_entity("b1"))])]
    p, r, _ = triple_prf(world, truth, alignment={})
    assert p == 0.0 and r == 0.0


def test_rmse_inf_when_no_alignment() -> None:
    world = [StateSnapshot(sim_time=1.0, triples=[])]
    truth = [StateSnapshot(sim_time=1.0, triples=[])]
    assert math.isinf(position_rmse(world, truth, {}))
