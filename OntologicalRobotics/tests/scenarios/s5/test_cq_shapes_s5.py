"""S5 CQ回帰＋クラス固有SHACL（H-1 / SCENARIOS.md §6）。"""

import pytest
import yaml

from orx.common import iri
from orx.common.config import load_config
from orx.common.paths import repo_root, shapes_dir
from orx.common.seeding import SeedTree
from orx.exp.suites.s5_hospital.graph import build_custody_graph
from orx.exp.suites.s5_hospital.model import S5World

CQ_DIR = repo_root() / "tasks" / "competency_questions" / "s5_hospital"


@pytest.fixture(scope="module")
def graph_world():
    world = load_config(repo_root() / "configs/world/s5_hospital.yaml", S5World)
    g = build_custody_graph(world, SeedTree(501))
    return g, world


def test_cq_files_parse() -> None:
    files = sorted(CQ_DIR.glob("*.yaml"))
    assert len(files) >= 2
    for f in files:
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "question" in d and "sparql" in d


def test_cq_prohibitions(graph_world) -> None:
    g, world = graph_world
    cq = yaml.safe_load((CQ_DIR / "cq_s5_prohibitions.yaml").read_text(encoding="utf-8"))
    rows = g.query(cq["sparql"])
    got = {(str(r["item"]), str(r["zoneclass"])) for r in rows}
    expected = {(n.item_class, n.forbidden_zone_class) for n in world.norms}
    assert got == expected


def test_cq_custody_chain_complete(graph_world) -> None:
    """H6/監査: 規制薬物搬送の custody 連鎖が全通行ステップ分記録されている。"""
    g, world = graph_world
    cq = yaml.safe_load((CQ_DIR / "cq_s5_custody_chain.yaml").read_text(encoding="utf-8"))
    rows = g.query(cq["sparql"])
    # 規制薬物搬送 t1 の連鎖が >=3 ステップ（pharmacy→secure_corr1→secure_corr2→ward_a）
    t1_item = iri.entity("transportitem", "t1")
    t1_steps = [r for r in rows if str(r["item"]) == t1_item]
    assert len(t1_steps) >= 3
    _ = world


def test_class_specific_shapes_conform(graph_world) -> None:
    g, _ = graph_world
    conforms, report = g.validate_shacl()
    assert conforms, report


def test_custody_step_missing_zone_fails() -> None:
    """atZone を欠く CustodyStep は SHACL 不適合。"""
    import pyshacl
    import rdflib

    NORM = rdflib.Namespace("https://orx.local/onto/norm#")
    data = rdflib.Graph()
    step = rdflib.URIRef(iri.entity("custodystep", "broken"))
    data.add((step, rdflib.RDF.type, NORM.CustodyStep))
    data.add((step, NORM.custodyOf, rdflib.URIRef(iri.entity("transportitem", "t1"))))  # atZone欠落
    shapes = rdflib.Graph()
    for path in sorted(shapes_dir().glob("*.ttl")):
        shapes.parse(path, format="turtle")
    conforms, _, _ = pyshacl.validate(data_graph=data, shacl_graph=shapes, inference="none")
    assert not conforms
