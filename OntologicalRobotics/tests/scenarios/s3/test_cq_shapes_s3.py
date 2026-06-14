"""S3 CQ回帰＋クラス固有SHACL（H-1 / SCENARIOS.md §6）。"""

import pytest
import yaml

from orx.common import iri
from orx.common.config import load_config
from orx.common.paths import repo_root, shapes_dir
from orx.common.seeding import SeedTree
from orx.exp.suites.s3_multi_vendor.graph import build_capability_graph
from orx.exp.suites.s3_multi_vendor.model import S3World

CQ_DIR = repo_root() / "tasks" / "competency_questions" / "s3_multi_vendor"


@pytest.fixture(scope="module")
def graph_world():
    world = load_config(repo_root() / "configs/world/s3_multi_vendor.yaml", S3World)
    g = build_capability_graph(world, SeedTree(301))
    return g, world


def test_cq_files_parse() -> None:
    files = sorted(CQ_DIR.glob("*.yaml"))
    assert len(files) >= 2
    for f in files:
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "question" in d and "sparql" in d


def test_cq_process_requirements(graph_world) -> None:
    g, world = graph_world
    cq = yaml.safe_load((CQ_DIR / "cq_s3_process_requirements.yaml").read_text(encoding="utf-8"))
    rows = g.query(cq["sparql"])
    materials = {str(r["material"]) for r in rows}
    assert materials == {p.material for p in world.products.values()}


def test_cq_matching_capability_is_cross_vendor(graph_world) -> None:
    """段取り替え後(engine 12kg)に適合する機体は a1 を除く b1,c1（語彙横断）。"""
    g, world = graph_world
    cq = yaml.safe_load((CQ_DIR / "cq_s3_matching_capability.yaml").read_text(encoding="utf-8"))
    rows = g.query(cq["sparql"])
    engine_req = iri.entity("processreq", "engine_block")
    matched = {iri.parse_entity(str(r["robot"]))[1] for r in rows if str(r["req"]) == engine_req}
    assert matched == {"cobot_b1", "agv_c1"}
    assert "arm_a1" not in matched  # 可搬上限で不適格


def test_class_specific_shapes_conform(graph_world) -> None:
    g, _ = graph_world
    conforms, report = g.validate_shacl()
    assert conforms, report


def test_process_requirement_missing_payload_fails() -> None:
    """requiresPayloadKg を欠く ProcessRequirement は SHACL 不適合。"""
    import pyshacl
    import rdflib

    CAP = rdflib.Namespace("https://orx.local/onto/cap#")
    data = rdflib.Graph()
    req = rdflib.URIRef(iri.entity("processreq", "broken"))
    data.add((req, rdflib.RDF.type, CAP.ProcessRequirement))
    data.add((req, CAP.requiresMaterial, rdflib.Literal("metal")))  # payload 欠落
    shapes = rdflib.Graph()
    for path in sorted(shapes_dir().glob("*.ttl")):
        shapes.parse(path, format="turtle")
    conforms, _, _ = pyshacl.validate(data_graph=data, shacl_graph=shapes, inference="none")
    assert not conforms
