"""S6 CQ回帰＋クラス固有SHACL（H-1 / SCENARIOS.md §6）。"""

import pytest
import yaml

from orx.common import iri
from orx.common.config import load_config
from orx.common.paths import repo_root, shapes_dir
from orx.common.seeding import SeedTree
from orx.exp.suites.s6_recycling import generator
from orx.exp.suites.s6_recycling.graph import build_routing_graph
from orx.exp.suites.s6_recycling.model import S6World

CQ_DIR = repo_root() / "tasks" / "competency_questions" / "s6_recycling"


@pytest.fixture(scope="module")
def graph_world():
    world = load_config(repo_root() / "configs/world/s6_recycling.yaml", S6World)
    ep = generator.generate_episode(world, seed=601, sigma=0.2)
    g = build_routing_graph(world, ep, SeedTree(601), confidence_threshold=0.15)
    return g, world


def test_cq_files_parse() -> None:
    files = sorted(CQ_DIR.glob("*.yaml"))
    assert len(files) >= 2
    for f in files:
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "question" in d and "sparql" in d


def test_cq_disposal_route_matches_regulation(graph_world) -> None:
    g, world = graph_world
    cq = yaml.safe_load((CQ_DIR / "cq_s6_disposal_route.yaml").read_text(encoding="utf-8"))
    rows = g.query(cq["sparql"])
    got = {(iri.parse_entity(r["cls"])[1], iri.parse_entity(r["lane"])[1]) for r in rows}
    expected = {(c, lane) for c, lane in world.disposal_route.items()}
    assert got == expected


def test_class_specific_shapes_conform(graph_world) -> None:
    g, _ = graph_world
    conforms, report = g.validate_shacl()
    assert conforms, report


def test_regulated_class_requires_route() -> None:
    """disposalRoute を欠く RegulatedClass は SHACL 不適合。"""
    import pyshacl
    import rdflib

    BIZ = rdflib.Namespace("https://orx.local/onto/biz#")
    data = rdflib.Graph()
    rc = rdflib.URIRef(iri.entity("regclass", "battery"))
    data.add((rc, rdflib.RDF.type, BIZ.RegulatedClass))  # disposalRoute 欠落
    shapes = rdflib.Graph()
    for path in sorted(shapes_dir().glob("*.ttl")):
        shapes.parse(path, format="turtle")
    conforms, _, _ = pyshacl.validate(data_graph=data, shacl_graph=shapes, inference="none")
    assert not conforms
