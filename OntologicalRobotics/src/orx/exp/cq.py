"""コンピテンシー質問（CQ）ランナー — オントロジーの仕様＝回帰テスト。

各CQは YAML（自然言語＋期待SPARQL＋真値導出規則）。SPARQLは世界グラフの
CURRENT_GRAPH（物質化された現在信念）に対して実行し、真値導出は oracle の
TruthState から行う。両者の一致が合格。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from orx.common.config import RunConfig
from orx.common.paths import cq_dir
from orx.common.schemas import StrictModel, TruthState
from orx.common.seeding import SeedTree
from orx.exp.episode import _Pipeline
from orx.kg.world_graph import WorldGraph
from orx.replay.io import RunReader


class CQDef(StrictModel):
    id: str
    question: str
    sparql: str
    truth_fn: str
    truth_args: dict[str, str] = {}
    compare: Literal["set", "count"] = "set"


class CQResult(StrictModel):
    cq_id: str
    question: str
    passed: bool
    got: str
    expected: str


# ----------------------------------------------------------- 真値導出レジストリ


def _barcodes_in_zone(truth: TruthState, zone: str) -> set[str]:
    return {o.barcode for o in truth.objects if o.zone == zone and o.barcode is not None}


def _count_in_zone(truth: TruthState, zone: str) -> int:
    return sum(1 for o in truth.objects if o.zone == zone)


def _all_barcodes(truth: TruthState) -> set[str]:
    return {o.barcode for o in truth.objects if o.barcode is not None}


def _empty(truth: TruthState) -> set[str]:
    return set()


TRUTH_FNS: dict[str, Callable[..., set[str] | int]] = {
    "barcodes_in_zone": _barcodes_in_zone,
    "count_in_zone": _count_in_zone,
    "all_barcodes": _all_barcodes,
    "empty": _empty,
}


# ------------------------------------------------------------------- ランナー


def load_cqs(directory: Path | None = None) -> list[CQDef]:
    import yaml

    root = directory or cq_dir()
    cqs: list[CQDef] = []
    for path in sorted(root.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        cqs.append(CQDef.model_validate(data))
    if not cqs:
        raise FileNotFoundError(f"CQ定義がありません: {root}")
    return cqs


def graph_from_run(run_dir: Path) -> tuple[WorldGraph, TruthState]:
    """記録済みrunの知覚イベントから世界グラフを再構築し、最終真値と共に返す。"""
    reader = RunReader(run_dir)
    config: RunConfig = reader.config()
    stage = _Pipeline(config, SeedTree(config.root_seed))
    for event in reader.events():
        stage.consume_event(event, writer=None)
    truth_states = list(reader.truth_states())
    if not truth_states:
        raise ValueError(f"truth ストリームが空です: {run_dir}")
    final_truth = truth_states[-1]
    stage.graph.refresh_current_graph(final_truth.sim_time)
    return stage.graph, final_truth


def run_cq(graph: WorldGraph, truth: TruthState, cq: CQDef) -> CQResult:
    fn = TRUTH_FNS.get(cq.truth_fn)
    if fn is None:
        raise ValueError(
            f"CQ {cq.id}: 未知の truth_fn {cq.truth_fn!r}（対応: {sorted(TRUTH_FNS)}）"
        )
    expected = fn(truth, **cq.truth_args)
    rows = graph.query(cq.sparql)
    if cq.compare == "count":
        got: set[str] | int = int(next(iter(rows[0].values()))) if rows else 0
    else:
        got = {next(iter(r.values())) for r in rows if r}
    passed = got == expected
    return CQResult(
        cq_id=cq.id,
        question=cq.question,
        passed=passed,
        got=_fmt(got),
        expected=_fmt(expected),
    )


def run_all_cqs(run_dir: Path, directory: Path | None = None) -> list[CQResult]:
    graph, truth = graph_from_run(run_dir)
    return [run_cq(graph, truth, cq) for cq in load_cqs(directory)]


def _fmt(value: set[str] | int) -> str:
    if isinstance(value, int):
        return str(value)
    return "{" + ", ".join(sorted(value)) + "}"
