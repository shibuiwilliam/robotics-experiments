"""SHACL 3本（単一ゾーン、危険物隣接禁止、ゾーン容量）の適合/違反グラフ対。"""

from pathlib import Path

import pytest
import rdflib

from gtwm.kg.store import (  # noqa: PLC2701 — 内部関数を直接検査する
    ValidationReport,
    _validate_graph,
)

pytestmark = pytest.mark.unit

SHAPES_DIR = Path(__file__).resolve().parents[2] / "ontology" / "shapes"
GT = "https://example.org/gtwm/gt#"


def _belief_graph(facts: list[tuple[str, str, str]]) -> rdflib.Graph:
    """(subject, predicate, object) の平坦事実を、confidence=1.0 の Belief 具象化に包んで返す。"""
    from datetime import datetime

    from gtwm.kg.schema import Belief

    g = rdflib.Graph()
    for s, p, o in facts:
        belief = Belief(
            subject=s,
            predicate=p,
            object=o,
            confidence=1.0,
            source="human",
            valid_from=datetime(2026, 1, 1),
            transaction_time=datetime(2026, 1, 1),
        )
        for triple in belief.to_reified_triples():
            g.add(triple)
    return g


def _validate(facts: list[tuple[str, str, str]], shape_file: str) -> ValidationReport:
    return _validate_graph(_belief_graph(facts), [SHAPES_DIR / shape_file])


def test_single_zone_shape_conforms_with_one_zone() -> None:
    report = _validate(
        [
            ("gt:Pallet_0001", "rdf:type", "gt:Pallet"),
            ("gt:Pallet_0001", "gt:currentZone", "gt:Zone_Storage_A"),
        ],
        "single_zone.ttl",
    )
    assert report.conforms


def test_single_zone_shape_violates_with_two_zones() -> None:
    report = _validate(
        [
            ("gt:Pallet_0001", "rdf:type", "gt:Pallet"),
            ("gt:Pallet_0001", "gt:currentZone", "gt:Zone_Storage_A"),
            ("gt:Pallet_0001", "gt:currentZone", "gt:Zone_Storage_B"),
        ],
        "single_zone.ttl",
    )
    # NOTE: sh:targetClass gt:Pallet が発火するには rdf:type gt:Pallet の宣言が必要。
    assert not report.conforms
    assert len(report.violations) >= 1


def test_hazmat_adjacency_shape_conforms_when_compatible() -> None:
    facts = [
        ("gt:Slot_A1", "rdf:type", "gt:Slot"),
        ("gt:Slot_A2", "rdf:type", "gt:Slot"),
        ("gt:Slot_A1", "gt:adjacentTo", "gt:Slot_A2"),
        ("gt:Slot_A1", "gt:holds", "gt:Pallet_0001"),
        ("gt:Slot_A2", "gt:holds", "gt:Pallet_0002"),
        ("gt:Pallet_0001", "gt:hazClass", "gt:HazClassA"),
        ("gt:Pallet_0002", "gt:hazClass", "gt:HazClassA"),
    ]
    report = _validate(facts, "hazmat_adjacency.ttl")
    assert report.conforms


def test_hazmat_adjacency_shape_violates_when_incompatible() -> None:
    facts = [
        ("gt:Slot_A1", "rdf:type", "gt:Slot"),
        ("gt:Slot_A2", "rdf:type", "gt:Slot"),
        ("gt:Slot_A1", "gt:adjacentTo", "gt:Slot_A2"),
        ("gt:Slot_A1", "gt:holds", "gt:Pallet_0001"),
        ("gt:Slot_A2", "gt:holds", "gt:Pallet_0002"),
        ("gt:Pallet_0001", "gt:hazClass", "gt:HazClassA"),
        ("gt:Pallet_0002", "gt:hazClass", "gt:HazClassB"),
        ("gt:HazClassA", "gt:incompatibleWith", "gt:HazClassB"),
    ]
    report = _validate(facts, "hazmat_adjacency.ttl")
    assert not report.conforms
    assert len(report.violations) >= 1


def _zone_capacity_graph(capacity: int, n_pallets: int) -> rdflib.Graph:
    """gt:capacity は SPARQL の数値比較に使うため xsd:integer に解釈される文字列で渡す。"""
    from datetime import datetime

    from gtwm.kg.schema import Belief

    g = rdflib.Graph()
    static_facts = [
        ("gt:Zone_Storage_A", "rdf:type", "gt:Zone"),
        ("gt:Zone_Storage_A", "gt:capacity", str(capacity)),
    ]
    for s, p, o in static_facts:
        for triple in Belief(
            subject=s,
            predicate=p,
            object=o,
            confidence=1.0,
            source="human",
            valid_from=datetime(2026, 1, 1),
            transaction_time=datetime(2026, 1, 1),
        ).to_reified_triples():
            g.add(triple)

    for i in range(1, n_pallets + 1):
        for triple in Belief(
            subject=f"gt:Pallet_{i:04d}",
            predicate="gt:currentZone",
            object="gt:Zone_Storage_A",
            confidence=1.0,
            source="human",
            valid_from=datetime(2026, 1, 1),
            transaction_time=datetime(2026, 1, 1),
        ).to_reified_triples():
            g.add(triple)
    return g


def test_zone_capacity_shape_conforms_within_capacity() -> None:
    g = _zone_capacity_graph(capacity=2, n_pallets=1)
    report = _validate_graph(g, [SHAPES_DIR / "zone_capacity.ttl"])
    assert report.conforms


def test_zone_capacity_shape_violates_when_exceeded() -> None:
    g = _zone_capacity_graph(capacity=1, n_pallets=2)
    report = _validate_graph(g, [SHAPES_DIR / "zone_capacity.ttl"])
    assert not report.conforms
    assert len(report.violations) >= 1
