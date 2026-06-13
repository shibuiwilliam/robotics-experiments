"""S2 CQ回帰＋クラス固有SHACL（H-1 / SCENARIOS.md §6）。"""


import pytest
import yaml

from orx.common.config import load_config
from orx.common.paths import repo_root, shapes_dir
from orx.common.seeding import SeedTree
from orx.exp.suites.s2_allergen import generator
from orx.exp.suites.s2_allergen.graph import build_contamination_graph
from orx.exp.suites.s2_allergen.model import S2World
from orx.oracle.scenarios.s2 import contamination_closure

CQ_DIR = repo_root() / "tasks" / "competency_questions" / "s2_allergen"


@pytest.fixture(scope="module")
def graph_and_world():
    world = load_config(repo_root() / "configs/world/s2_allergen.yaml", S2World)
    ep = generator.generate_episode(world, seed=201)
    g = build_contamination_graph(ep, world.allergens, SeedTree(201))
    return g, world, ep


def test_cq_files_parse() -> None:
    files = sorted(CQ_DIR.glob("*.yaml"))
    assert len(files) >= 2
    for f in files:
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "question" in d and "sparql" in d


def test_cq_contaminated_matches_oracle(graph_and_world) -> None:
    g, _world, ep = graph_and_world
    cq = yaml.safe_load((CQ_DIR / "cq_s2_contaminated.yaml").read_text(encoding="utf-8"))
    rows = g.query(cq["sparql"])
    from orx.common import iri

    got = {
        (iri.parse_entity(r["e"])[1], iri.parse_entity(r["allergen"])[1]) for r in rows
    }
    st = contamination_closure(ep.intrinsic, ep.contacts, ep.cleanings, ep.eval_time)
    expected = {(e, a) for e, alls in st.carried.items() for a in alls}
    assert got == expected


def test_class_specific_shapes_conform(graph_and_world) -> None:
    g, _world, _ep = graph_and_world
    conforms, report = g.validate_shacl()
    assert conforms, report


def test_contact_event_shape_rejects_single_party() -> None:
    """ContactEvent が当事者1名のみなら SHACL 不適合。"""
    import pyshacl
    import rdflib

    from orx.common import iri

    ST = rdflib.Namespace("https://orx.local/onto/st#")
    data = rdflib.Graph()
    ce = rdflib.URIRef(iri.entity("contact", "bad"))
    data.add((ce, rdflib.RDF.type, ST.ContactEvent))
    data.add((ce, ST.contactParty, rdflib.URIRef(iri.entity("object", "g_a"))))  # 1名のみ
    shapes = rdflib.Graph()
    for path in sorted(shapes_dir().glob("*.ttl")):
        shapes.parse(path, format="turtle")
    conforms, _, _ = pyshacl.validate(data_graph=data, shacl_graph=shapes, inference="none")
    assert not conforms
