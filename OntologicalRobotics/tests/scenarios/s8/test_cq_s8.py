"""S8 CQ回帰（キネティック層・PROJECT.md §6）: アクション後の custody/監査クエリ。

OR-full の実行後グラフに対し、custody 連鎖と アクション監査の CQ が期待結果を返すことを検証する。
語彙（CustodyStep / ActionExecution）が壊れればここが落ちる。
"""

from __future__ import annotations

import pytest
import yaml

from orx.common import iri
from orx.common.config import WorldConfig, load_config
from orx.common.paths import repo_root
from orx.common.seeding import SeedTree
from orx.exp.suites.s8_fulfillment import reference
from orx.exp.suites.s8_fulfillment.generator import (
    build_ontology_view,
    build_vendor_view,
)
from orx.kg.world_graph import CURRENT_GRAPH

CQ_DIR = repo_root() / "tasks" / "competency_questions" / "s8_fulfillment"


@pytest.fixture(scope="module")
def or_full_graph():
    world: WorldConfig = load_config(repo_root() / "configs/world/s8_fulfillment.yaml", WorldConfig)
    ov, vv = build_ontology_view(world), build_vendor_view(world)
    _final, _receipts, _wb, graph, _rec = reference.run_condition(
        "OR-full", world, ov, vv, SeedTree(101)
    )
    graph.refresh_current_graph(100.0)
    return graph


def test_cq_files_parse() -> None:
    files = sorted(CQ_DIR.glob("*.yaml"))
    assert len(files) >= 2
    for f in files:
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "question" in d and "sparql" in d
        assert f"GRAPH <{CURRENT_GRAPH}>" in d["sparql"]


def test_cq_custody_chain(or_full_graph) -> None:
    cq = yaml.safe_load((CQ_DIR / "cq_s8_custody_chain.yaml").read_text(encoding="utf-8"))
    rows = or_full_graph.query(cq["sparql"])
    items = {r["item"] for r in rows}
    for bc in ("BC-HV", "BC-RG", "BC-PI"):
        assert iri.entity("object", bc) in items


def test_cq_action_audit(or_full_graph) -> None:
    cq = yaml.safe_load((CQ_DIR / "cq_s8_action_audit.yaml").read_text(encoding="utf-8"))
    rows = or_full_graph.query(cq["sparql"])
    statuses = {(r["obj"], r["status"]) for r in rows}
    # OR-full は3アクションを applied で書き戻す
    for bc in ("BC-HV", "BC-RG", "BC-PI"):
        assert (iri.entity("object", bc), "applied") in statuses
