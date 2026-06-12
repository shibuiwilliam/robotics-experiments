"""SHACL shapes 自体のテスト: 不適合データを正しく拒否すること。"""

import pyshacl
import rdflib

from orx.common import iri
from orx.common.paths import shapes_dir

PROV = rdflib.Namespace("https://orx.local/onto/prov#")
XSD_DOUBLE = rdflib.URIRef(iri.XSD_DOUBLE)


def shapes_graph() -> rdflib.Graph:
    g = rdflib.Graph()
    for path in sorted(shapes_dir().glob("*.ttl")):
        g.parse(path, format="turtle")
    return g


def validate(data: rdflib.Graph) -> tuple[bool, str]:
    conforms, _, text = pyshacl.validate(
        data_graph=data, shacl_graph=shapes_graph(), inference="none"
    )
    return bool(conforms), str(text)


def claim_node(g: rdflib.Graph) -> rdflib.URIRef:
    node = rdflib.URIRef(iri.claim("test1"))
    g.add((node, rdflib.RDF.type, PROV.Claim))
    return node


def test_complete_claim_conforms() -> None:
    g = rdflib.Graph()
    node = claim_node(g)
    g.add((node, PROV.assertedBy, rdflib.URIRef(iri.entity("agent", "a1"))))
    g.add((node, PROV.confidence, rdflib.Literal("0.9", datatype=XSD_DOUBLE)))
    g.add((node, PROV.observedAt, rdflib.Literal("1.0", datatype=XSD_DOUBLE)))
    conforms, report = validate(g)
    assert conforms, report


def test_missing_provenance_rejected() -> None:
    g = rdflib.Graph()
    node = claim_node(g)
    g.add((node, PROV.confidence, rdflib.Literal("0.9", datatype=XSD_DOUBLE)))
    g.add((node, PROV.observedAt, rdflib.Literal("1.0", datatype=XSD_DOUBLE)))
    conforms, _ = validate(g)
    assert not conforms  # assertedBy 欠落


def test_missing_confidence_rejected() -> None:
    g = rdflib.Graph()
    node = claim_node(g)
    g.add((node, PROV.assertedBy, rdflib.URIRef(iri.entity("agent", "a1"))))
    g.add((node, PROV.observedAt, rdflib.Literal("1.0", datatype=XSD_DOUBLE)))
    conforms, _ = validate(g)
    assert not conforms


def test_out_of_range_confidence_rejected() -> None:
    g = rdflib.Graph()
    node = claim_node(g)
    g.add((node, PROV.assertedBy, rdflib.URIRef(iri.entity("agent", "a1"))))
    g.add((node, PROV.confidence, rdflib.Literal("1.5", datatype=XSD_DOUBLE)))
    g.add((node, PROV.observedAt, rdflib.Literal("1.0", datatype=XSD_DOUBLE)))
    conforms, _ = validate(g)
    assert not conforms


def test_owl_same_as_rejected() -> None:
    g = rdflib.Graph()
    g.add(
        (
            rdflib.URIRef(iri.entity("object", "e1")),
            rdflib.OWL.sameAs,
            rdflib.URIRef(iri.entity("object", "e2")),
        )
    )
    conforms, _ = validate(g)
    assert not conforms
