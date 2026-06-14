"""S4 CQ回帰＋クラス固有SHACL（H-1 / SCENARIOS.md §6）。"""

import pytest
import yaml

from orx.common import iri
from orx.common.config import load_config
from orx.common.paths import repo_root, shapes_dir
from orx.common.seeding import SeedTree
from orx.exp.suites.s4_inspection import generator
from orx.exp.suites.s4_inspection.graph import build_inspection_graph
from orx.exp.suites.s4_inspection.model import S4World

CQ_DIR = repo_root() / "tasks" / "competency_questions" / "s4_inspection"


@pytest.fixture(scope="module")
def graph_world():
    world = load_config(repo_root() / "configs/world/s4_inspection.yaml", S4World)
    ep = generator.generate_episode(world, seed=401, position_noise=0.4)
    g = build_inspection_graph(world, ep, SeedTree(401))
    return g, world


def test_cq_files_parse() -> None:
    files = sorted(CQ_DIR.glob("*.yaml"))
    assert len(files) >= 2
    for f in files:
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "question" in d and "sparql" in d


def test_cq_system_sop_lists_all_assets(graph_world) -> None:
    g, world = graph_world
    cq = yaml.safe_load((CQ_DIR / "cq_s4_system_sop.yaml").read_text(encoding="utf-8"))
    rows = g.query(cq["sparql"])
    assets = {iri.parse_entity(str(r["asset"]))[1] for r in rows}
    assert assets == {a.asset_id for a in world.assets}


def test_cq_asset_state_workorders_for_anomalies(graph_world) -> None:
    """H2: 同一化＋調停の結果、真の異常資産にだけ作業指示が起票されている。"""
    g, world = graph_world
    cq = yaml.safe_load((CQ_DIR / "cq_s4_asset_state.yaml").read_text(encoding="utf-8"))
    rows = g.query(cq["sparql"])
    wo_assets = {iri.parse_entity(str(r["asset"]))[1] for r in rows}
    true_anom = {a.asset_id for a in world.assets if a.true_anomaly}
    assert wo_assets == true_anom


def test_class_specific_shapes_conform(graph_world) -> None:
    g, _ = graph_world
    conforms, report = g.validate_shacl()
    assert conforms, report


def test_workorder_missing_sop_fails() -> None:
    """appliesSop を欠く WorkOrder は SHACL 不適合。"""
    import pyshacl
    import rdflib

    BIZ = rdflib.Namespace("https://orx.local/onto/biz#")
    data = rdflib.Graph()
    wo = rdflib.URIRef(iri.entity("workorder", "broken"))
    data.add((wo, rdflib.RDF.type, BIZ.WorkOrder))
    data.add((wo, BIZ.forAsset, rdflib.URIRef(iri.entity("asset", "V-205"))))  # appliesSop 欠落
    shapes = rdflib.Graph()
    for path in sorted(shapes_dir().glob("*.ttl")):
        shapes.parse(path, format="turtle")
    conforms, _, _ = pyshacl.validate(data_graph=data, shacl_graph=shapes, inference="none")
    assert not conforms
