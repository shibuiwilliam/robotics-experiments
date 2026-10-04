"""信念・知識層。二時制アサーション API、名前付きグラフ、PROV、潜在索引、再接地。

責務: イベントログ、二時制・様相・来歴付き知識グラフ、潜在索引、再接地。
非責務: 推論ロジック、UI。SPARQL 文字列はこの層の内側に閉じる。

上位層（``reasoning``）は :class:`~sbp.knowledge.assertions.AssertionStore` の 3 メソッド
（``assert_`` / ``retract`` / ``query``）経由でのみ永続化する。
"""

from __future__ import annotations

from sbp.knowledge.artifacts import (
    ArtifactStore,
    LocalArtifactStore,
    S3ArtifactStore,
    parse_s3_uri,
)
from sbp.knowledge.assertions import AssertionRow, AssertionStore, current_beliefs
from sbp.knowledge.concepts import ConceptProposalRecord, ConceptProposalStore
from sbp.knowledge.decisions import DecisionKind, DecisionQueue, DecisionRow
from sbp.knowledge.discrepancies import DiscrepancyRecord, DiscrepancyStore
from sbp.knowledge.entities import entities_of_type, site_prefix
from sbp.knowledge.expectations import ExpectationRecord, ExpectationStore
from sbp.knowledge.graphs import belief_graph, is_scenario_graph, scenario_graph, schema_graph
from sbp.knowledge.hybrid import HybridPlan, PlanStrategy, Selectivity
from sbp.knowledge.hybrid import plan as plan_hybrid
from sbp.knowledge.latent import LatentIndex, LatentNeighbour, LatentRegistration
from sbp.knowledge.metrics_store import MetricStore
from sbp.knowledge.observations import ObservationQueue, ObservationRow
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
from sbp.knowledge.relations import (
    AggregationByFluent,
    PartWholeEdge,
    aggregation_rules,
    group_by_whole,
    part_whole_edges,
    situation_members,
)
from sbp.knowledge.store import EmbeddedGraphStore, GraphStore, HttpGraphStore
from sbp.knowledge.watermarks import Watermark, WatermarkStore

__all__ = [
    "AggregationByFluent",
    "ArtifactStore",
    "AssertionRow",
    "AssertionStore",
    "CompatibilityMatrix",
    "ConceptProposalRecord",
    "ConceptProposalStore",
    "DecisionKind",
    "DecisionQueue",
    "DecisionRow",
    "DiffEntry",
    "DiscrepancyRecord",
    "DiscrepancyStore",
    "EmbeddedGraphStore",
    "ExpectationRecord",
    "ExpectationStore",
    "GraphStore",
    "HttpGraphStore",
    "HybridPlan",
    "LatentIndex",
    "LatentNeighbour",
    "LatentRegistration",
    "LocalArtifactStore",
    "MetricStore",
    "ObservationQueue",
    "ObservationRow",
    "PartWholeEdge",
    "PlanStrategy",
    "RegroundingPlan",
    "RegroundingReport",
    "RegroundingTrigger",
    "S3ArtifactStore",
    "ScopeEstimate",
    "Selectivity",
    "Watermark",
    "WatermarkStore",
    "aggregation_rules",
    "belief_graph",
    "current_beliefs",
    "entities_of_type",
    "group_by_whole",
    "is_scenario_graph",
    "parse_s3_uri",
    "part_whole_edges",
    "plan_hybrid",
    "plan_regrounding",
    "scenario_graph",
    "schema_graph",
    "site_prefix",
    "situation_members",
    "summarise_regrounding",
]
