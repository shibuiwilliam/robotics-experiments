"""S7 CQ回帰＋クラス固有SHACL（H-1 / SCENARIOS.md §6）。"""

import pytest
import yaml

from orx.common import iri
from orx.common.config import load_config
from orx.common.paths import repo_root, shapes_dir
from orx.common.seeding import SeedTree
from orx.exp.suites.s7_ownership.graph import build_ownership_graph
from orx.exp.suites.s7_ownership.model import S7World

CQ_DIR = repo_root() / "tasks" / "competency_questions" / "s7_ownership"


@pytest.fixture(scope="module")
def graph_world():
    world = load_config(repo_root() / "configs/world/s7_ownership.yaml", S7World)
    g = build_ownership_graph(world, SeedTree(701))
    return g, world


def test_cq_files_parse() -> None:
    files = sorted(CQ_DIR.glob("*.yaml"))
    assert len(files) >= 2
    for f in files:
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "question" in d and "sparql" in d


def test_cq_ownership_links_owner_item_zone(graph_world) -> None:
    g, world = graph_world
    cq = yaml.safe_load((CQ_DIR / "cq_s7_ownership.yaml").read_text(encoding="utf-8"))
    rows = g.query(cq["sparql"])
    owners = {iri.parse_entity(str(r["resident"]))[1] for r in rows}
    assert owners == set(world.residents)


def test_cq_delivery_room_answers_whose_where(graph_world) -> None:
    """H6: 各入居者の所有物→居室が引ける（所有関係が無いと答えられない）。"""
    g, world = graph_world
    cq = yaml.safe_load((CQ_DIR / "cq_s7_delivery_room.yaml").read_text(encoding="utf-8"))
    rows = g.query(cq["sparql"])
    rooms = {iri.parse_entity(str(r["resident"]))[1]: str(r["room"]) for r in rows}
    assert rooms == world.rooms


def test_class_specific_shapes_conform(graph_world) -> None:
    g, _ = graph_world
    conforms, report = g.validate_shacl()
    assert conforms, report


def test_owns_non_personalitem_fails() -> None:
    """owns の値が PersonalItem でなければ SHACL 不適合。"""
    import pyshacl
    import rdflib

    BIZ = rdflib.Namespace("https://orx.local/onto/biz#")
    data = rdflib.Graph()
    r = rdflib.URIRef(iri.entity("resident", "x"))
    data.add((r, rdflib.RDF.type, BIZ.Resident))
    data.add((r, BIZ.assignedRoom, rdflib.Literal("room_999")))
    data.add((r, BIZ.owns, rdflib.URIRef(iri.entity("widget", "w1"))))  # PersonalItem型でない
    shapes = rdflib.Graph()
    for path in sorted(shapes_dir().glob("*.ttl")):
        shapes.parse(path, format="turtle")
    conforms, _, _ = pyshacl.validate(data_graph=data, shacl_graph=shapes, inference="none")
    assert not conforms
