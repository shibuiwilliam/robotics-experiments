from datetime import datetime

import pytest
import rdflib

from gtwm.kg.schema import GT, Belief

pytestmark = pytest.mark.unit


def test_belief_reified_triples_roundtrip() -> None:
    belief = Belief(
        subject="gt:Pallet_0042",
        predicate="gt:currentZone",
        object="gt:Zone_Storage_A",
        confidence=0.93,
        source="wm",
        valid_from=datetime(2026, 1, 1, 0, 0, 0),
        transaction_time=datetime(2026, 1, 1, 0, 0, 1),
    )
    triples = belief.to_reified_triples()
    g = rdflib.Graph()
    for t in triples:
        g.add(t)

    assert len(list(g.subjects(rdflib.RDF.type, GT.Belief))) == 1
    stmt = next(g.subjects(rdflib.RDF.type, GT.Belief))
    assert (stmt, rdflib.RDF.subject, GT.Pallet_0042) in g
    assert (stmt, rdflib.RDF.predicate, GT.currentZone) in g
    assert (stmt, rdflib.RDF.object, GT.Zone_Storage_A) in g
    confidences = list(g.objects(stmt, GT.confidence))
    assert float(confidences[0]) == pytest.approx(0.93)
    assert (stmt, GT.source, GT.WM) in g


def test_belief_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValueError):
        Belief(
            subject="gt:Pallet_0042",
            predicate="gt:currentZone",
            object="gt:Zone_Storage_A",
            confidence=1.5,
            source="wm",
            valid_from=datetime(2026, 1, 1),
            transaction_time=datetime(2026, 1, 1),
        )
