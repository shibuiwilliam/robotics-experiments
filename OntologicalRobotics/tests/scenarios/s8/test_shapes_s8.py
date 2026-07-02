"""S8 ActionExecution の SHACL shape テスト: 不適合フィクスチャを拒否する。"""

import pyshacl
import rdflib

from orx.common import iri
from orx.common.paths import shapes_dir

CAP = rdflib.Namespace("https://orx.local/onto/cap#")
XSD = rdflib.Namespace("http://www.w3.org/2001/XMLSchema#")


def shapes_graph() -> rdflib.Graph:
    g = rdflib.Graph()
    for path in sorted(shapes_dir().glob("*.ttl")):
        g.parse(path, format="turtle")
    return g


def validate(data: rdflib.Graph) -> bool:
    conforms, _, _ = pyshacl.validate(data_graph=data, shacl_graph=shapes_graph(), inference="none")
    return bool(conforms)


def _exec(g: rdflib.Graph, status: str = "applied", with_acted_on: bool = True):
    node = rdflib.URIRef(iri.entity("action", "a1"))
    g.add((node, rdflib.RDF.type, CAP.ActionExecution))
    g.add((node, CAP.executedByRobot, rdflib.URIRef(iri.entity("robot", "r1"))))
    if with_acted_on:
        g.add((node, CAP.actedOn, rdflib.URIRef(iri.entity("object", "BC-X"))))
    g.add((node, CAP.actionStatus, rdflib.Literal(status, datatype=XSD.string)))
    return node


def test_valid_action_execution_conforms() -> None:
    g = rdflib.Graph()
    _exec(g, status="applied")
    assert validate(g)


def test_action_without_acted_on_rejected() -> None:
    g = rdflib.Graph()
    _exec(g, with_acted_on=False)  # actedOn 欠落
    assert not validate(g)


def test_action_with_bad_status_rejected() -> None:
    g = rdflib.Graph()
    _exec(g, status="boom")  # 不正な状態（applied|staged|rejected 以外）
    assert not validate(g)
