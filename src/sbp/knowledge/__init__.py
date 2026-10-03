"""信念・知識層。二時制アサーション API、名前付きグラフ、PROV、潜在索引、再接地。

責務: イベントログ、二時制・様相・来歴付き知識グラフ、潜在索引、再接地。
非責務: 推論ロジック、UI。SPARQL 文字列はこの層の内側に閉じる。

上位層（``reasoning``）は :class:`~sbp.knowledge.assertions.AssertionStore` の 3 メソッド
（``assert_`` / ``retract`` / ``query``）経由でのみ永続化する。
"""

from __future__ import annotations

from sbp.knowledge.assertions import AssertionRow, AssertionStore
from sbp.knowledge.graphs import belief_graph, is_scenario_graph, scenario_graph, schema_graph
from sbp.knowledge.hybrid import HybridPlan, PlanStrategy, Selectivity
from sbp.knowledge.hybrid import plan as plan_hybrid
from sbp.knowledge.latent import LatentIndex, LatentNeighbour, LatentRegistration
from sbp.knowledge.regrounding import (
    CompatibilityMatrix,
    DiffEntry,
    RegroundingPlan,
    RegroundingReport,
    RegroundingTrigger,
    ScopeEstimate,
)
from sbp.knowledge.regrounding import plan as plan_regrounding
from sbp.knowledge.regrounding import summarise as summarise_regrounding
from sbp.knowledge.store import EmbeddedGraphStore, GraphStore, HttpGraphStore

__all__ = [
    "AssertionRow",
    "AssertionStore",
    "CompatibilityMatrix",
    "DiffEntry",
    "EmbeddedGraphStore",
    "GraphStore",
    "HttpGraphStore",
    "HybridPlan",
    "LatentIndex",
    "LatentNeighbour",
    "LatentRegistration",
    "PlanStrategy",
    "RegroundingPlan",
    "RegroundingReport",
    "RegroundingTrigger",
    "ScopeEstimate",
    "Selectivity",
    "belief_graph",
    "is_scenario_graph",
    "plan_hybrid",
    "plan_regrounding",
    "scenario_graph",
    "schema_graph",
    "summarise_regrounding",
]
